"""OpenAI Chat Completions transport; no silent retries or response caching.

API contract checked at https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create
Model: user selected GPT-5.4 nano for DEV, 2026-09-21. Total experiment API authorization: $10, recorded in the shared ledger configuration.
"""
from dataclasses import asdict
import base64
import http.client
from decimal import Decimal
import json
import os
import re
import time
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request

from .agent import BudgetStop, ModelError, Reply
from .artifacts import canonical, digest, write_once
from .budget import BudgetLedger, nusd


def credential_present(config):
    return bool(os.environ.get(config["model"]["key_env"], "").strip())


def load_credentials(path):
    """Explicit local file only; never execute a dotenv file as shell code."""
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        key, sep, value = line.removeprefix('export ').partition('=')
        if not sep or key.strip() not in {'OPENAI_API_KEY'}:
            continue
        os.environ[key.strip()] = value.strip().strip('\"\'')


def preflight(config):
    missing = []
    for name in ('id', 'endpoint'):
        if not config['model'].get(name):missing.append('model.'+name)
    if not credential_present(config):missing.append('credential:'+config['model']['key_env'])
    for name in ('authorized_usd','authorization_evidence','input_usd_per_million','output_usd_per_million','price_source'):
        if config['budget'].get(name) is None or config['budget'].get(name) == '':missing.append('budget.'+name)
    if config['phase'] not in config['budget'].get('authorized_phases',[]):
        missing.append('budget.authorization_for_phase:'+config['phase'])
    return {'ready':not missing,'missing':missing,'model':config['model']['id'],
            'paid_calls_made':0,'config_frozen':config.get('frozen',False)}


def check_model_access(config):
    """One non-inference model metadata GET; never print API error messages."""
    key=os.environ.get(config['model']['key_env'],'')
    request=urllib.request.Request('https://api.openai.com/v1/models/'+urllib.parse.quote(config['model']['id'],safe=''),
                                   headers={'Authorization':'Bearer '+key})
    try:
        with urllib.request.urlopen(request,timeout=20) as response:
            data=json.load(response)
        return {'authenticated':True,'model_id':data.get('id'),'inference_requests':0}
    except urllib.error.HTTPError as error:
        try:code=json.load(error).get('error',{}).get('code')
        except Exception:code='unparsed_error'
        return {'authenticated':False,'http_status':error.code,
                'error_code':code if code in {'invalid_api_key','model_not_found','insufficient_quota'} else 'other',
                'inference_requests':0}
    except (urllib.error.URLError,TimeoutError,OSError):
        return {'authenticated':False,'error_code':'network_error','inference_requests':0}


def seconds(value):
    """Numeric Retry-After or documented duration strings, e.g. 1m20.5s."""
    value=str(value or '')
    if re.fullmatch(r'\d+(?:\.\d+)?',value):return float(value)
    if not re.fullmatch(r'(?:\d+(?:\.\d+)?(?:ms|s|m|h))+',value):return None
    return sum(float(n)*{'ms':.001,'s':1,'m':60,'h':3600}[u]
               for n,u in re.findall(r'(\d+(?:\.\d+)?)(ms|s|m|h)',value))


def safe_headers(headers):
    allowed=('retry-after','x-ratelimit-limit-requests','x-ratelimit-limit-tokens',
             'x-ratelimit-remaining-requests','x-ratelimit-remaining-tokens',
             'x-ratelimit-reset-requests','x-ratelimit-reset-tokens',
             'x-ratelimit-limit-project-tokens','x-ratelimit-remaining-project-tokens',
             'x-ratelimit-reset-project-tokens')
    return {k:str(headers[k]) for k in allowed if headers.get(k) is not None and seconds(headers[k]) is not None}


class OpenAIModel:
    kind = 'live'

    def __init__(self, config, ledger, response_dir):
        status = preflight(config)
        if not status['ready']:raise BudgetStop('Missing live access or budget configuration')
        self.configuration = config['model']
        self.model_id = self.configuration['id']
        self.prices = config['budget']
        self.ledger = ledger
        self.response_dir = Path(response_dir)
        parsed = urllib.parse.urlsplit(self.configuration['endpoint'])
        if parsed.scheme != 'https' or parsed.hostname != 'api.openai.com' or parsed.username or parsed.password or parsed.query:
            raise ValueError('This transport accepts official OpenAI HTTPS endpoint only')
        self.key = os.environ[self.configuration['key_env']]
        self.ledger.db.execute('CREATE TABLE IF NOT EXISTS transport_state (model TEXT PRIMARY KEY, state TEXT NOT NULL)')
        self.ledger.db.commit()

    def _state(self):
        row=self.ledger.db.execute('SELECT state FROM transport_state WHERE model=?',(self.model_id,)).fetchone()
        return json.loads(row[0]) if row else {}

    def _record_limits(self, headers, error_code=None, fatal=False):
        old=self._state();now=time.time()
        delay=seconds(headers.get('retry-after'))
        state={'observed_at':now,'headers':headers,'fatal':fatal,
               'error_code':error_code,'consecutive_errors':old.get('consecutive_errors',0)+1 if error_code else 0}
        if error_code:
            # Shared cooldown survives worker exit; no nested request retries.
            jitter=int(digest([self.model_id,now])[:4],16)/65535
            state['blocked_until']=now+(delay if delay is not None else min(60,2**min(state['consecutive_errors'],6)))+jitter
        with self.ledger.db:
            self.ledger.db.execute('INSERT OR REPLACE INTO transport_state VALUES (?,?)',(self.model_id,canonical(state)))

    def _pace(self, token_upper_bound, call_id):
        state=self._state()
        if state.get('fatal'):raise ModelError('Provider access/configuration blocked; inspect sanitized transport record')
        now=time.time();deadline=state.get('blocked_until',0);headers=state.get('headers',{})
        for resource,needed in (('requests',1),('tokens',token_upper_bound),('project-tokens',token_upper_bound)):
            remaining=headers.get('x-ratelimit-remaining-'+resource)
            reset=seconds(headers.get('x-ratelimit-reset-'+resource))
            if remaining is not None and float(remaining)<needed and reset is not None:
                deadline=max(deadline,state['observed_at']+reset+.1)
        delay=max(0,deadline-now)
        if delay:
            write_once(self.response_dir/(digest(call_id)+'.pacing.json'),{'call_id':call_id,'delay_s':delay,
                       'deadline':deadline,'reason':'provider_capacity_or_cooldown','state':state})
            if delay>self.configuration.get('max_pacing_wait_s',60):
                raise ModelError('Provider wait exceeds bounded deadline; request deferred without submission')
            time.sleep(delay)

    def complete(self, messages, call_id):
        # Byte upper bound plus generous per-message/protocol margin, no assumed
        # cache discount at reservation time. Output bound includes reasoning.
        upper_input = len(canonical(messages).encode()) + 2048 + 128*len(messages)
        if upper_input > self.configuration['input_token_limit']:
            raise ModelError('Prompt exceeds conservative input safety bound')
        cap_output = self.configuration['max_output_tokens']
        self._pace(upper_input+cap_output,call_id)
        maximum = nusd((Decimal(upper_input)*Decimal(str(self.prices['input_usd_per_million'])) +
                        Decimal(cap_output)*Decimal(str(self.prices['output_usd_per_million'])))/1_000_000)
        self.ledger.reserve(call_id, maximum)
        payload = {'model':self.model_id,'messages':messages,'max_completion_tokens':cap_output,
                   'temperature':self.configuration['temperature'],
                   'reasoning_effort':self.configuration['reasoning_effort'],'store':False,'stream':False}
        request = urllib.request.Request(self.configuration['endpoint'], data=canonical(payload).encode(),
                         headers={'Authorization':'Bearer '+self.key,'Content-Type':'application/json'}, method='POST')
        try:
            with urllib.request.urlopen(request,timeout=self.configuration['request_timeout_s']) as response:
                try:
                    body = response.read()
                except http.client.IncompleteRead as error:
                    write_once(self.response_dir/(digest(call_id)+'.http-body.json'),
                               {'call_id':call_id,'incomplete':True,
                                'body_base64':base64.b64encode(error.partial).decode('ascii')})
                    raise
                # Persist received bytes before decoding: malformed/truncated JSON
                # still represents a submitted, potentially charged request.
                write_once(self.response_dir/(digest(call_id)+'.http-body.json'),
                           {'call_id':call_id,'body_base64':base64.b64encode(body).decode('ascii')})
                data = json.loads(body)
                request_id = response.headers.get('x-request-id')
                limits=safe_headers(response.headers)
        except urllib.error.HTTPError as error:
            # Error messages can echo credentials; retain whitelisted codes only.
            try:detail=json.load(error).get('error',{})
            except Exception:detail={}
            allowed={'invalid_api_key','insufficient_quota','rate_limit_exceeded','slow_down',
                     'server_is_overloaded','model_not_found','context_length_exceeded','unsupported_parameter'}
            code=detail.get('code');code=code if code in allowed else 'other'
            limits=safe_headers(error.headers)
            fatal=error.code in {400,401,403,404} or code=='insufficient_quota'
            self._record_limits(limits,code,fatal)
            self.ledger.unknown(call_id,'uncertain_http_'+str(error.code))
            write_once(self.response_dir/(digest(call_id)+'.error.json'),{'call_id':call_id,'http_status':error.code,
                       'error_code':code,'headers':limits,'fatal':fatal,'implicit_retries':0})
            raise ModelError('HTTP request failed; sanitized error artifact retained') from None
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            # A transport failure may still have consumed billable computation.
            self.ledger.unknown(call_id, 'uncertain_transport_error')
            from .runner import EpisodeTimeout
            if isinstance(error, EpisodeTimeout):raise
            raise ModelError(type(error).__name__) from None
        except BaseException:
            self.ledger.unknown(call_id, 'uncertain_interrupted_or_invalid_response')
            raise
        # Preserve provider output before parsing/settling; never persist headers/key.
        self._record_limits(limits)
        write_once(self.response_dir/(digest(call_id)+'.json'),
                   {'call_id':call_id,'request_id':request_id,'rate_limits':limits,'response':data})
        try:
            usage = data['usage']
            i, o = usage['prompt_tokens'], usage['completion_tokens']
            cached = usage.get('prompt_tokens_details',{}).get('cached_tokens',0)
            if any(type(v) is not int or v < 0 for v in (i,o,cached)) or cached > i:
                raise ValueError('Invalid usage')
            cached_rate = self.prices.get('cached_input_usd_per_million',self.prices['input_usd_per_million'])
            actual = nusd((Decimal(i-cached)*Decimal(str(self.prices['input_usd_per_million']))+
                           Decimal(cached)*Decimal(str(cached_rate))+Decimal(o)*Decimal(str(self.prices['output_usd_per_million'])))/1_000_000)
            self.ledger.settle(call_id,actual,usage)
            if data['model'] != self.model_id:
                raise ValueError('Provider returned a different model snapshot')
            content = data['choices'][0]['message']['content']
            if not isinstance(content,str):raise ValueError('Missing model output')
            return Reply(content,i,o,actual,request_id,self.model_id)
        except BudgetStop:raise
        except (KeyError,IndexError,TypeError,ValueError):
            self.ledger.unknown(call_id,'uncertain_invalid_response')
            raise ModelError('Invalid provider response; raw response retained') from None

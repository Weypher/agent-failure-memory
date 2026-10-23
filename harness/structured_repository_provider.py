"""Strict operation transport; original experiment transport remains unchanged.

The complete method preserves provider.py's raw retention and billing workflow,
adding a fixed schema and deterministic decoding. Requires separate stage review.
"""
import base64
import sqlite3
import http.client
import json
from decimal import Decimal
import urllib.error
import urllib.request
from .provider import OpenAIModel, safe_headers
from .agent import BudgetStop, ModelError, ModelOutputIncomplete, Reply
from .artifacts import canonical, digest, write_once
from .budget import nusd
from .repository_action import RESPONSE_FORMAT, input_upper_bound, decode, wire_messages

class StructuredRepositoryModel(OpenAIModel):
    # Subclasses swap the strict schema and decoder; transport, reservation,
    # retention and settlement are shared unchanged.
    RESPONSE_FORMAT = RESPONSE_FORMAT

    @staticmethod
    def decode_content(content):
        return decode(content)

    def input_bound(self, messages):
        return input_upper_bound(messages)

    def complete(self, messages, call_id):
        # Byte upper bound plus generous per-message/protocol margin, no assumed
        # cache discount at reservation time. Output bound includes reasoning.
        upper_input = self.input_bound(messages)
        if upper_input > self.configuration['input_token_limit']:
            raise ModelError('Prompt exceeds conservative input safety bound')
        cap_output = self.configuration['max_output_tokens']
        self._pace(upper_input+cap_output,call_id)
        maximum = nusd((Decimal(upper_input)*Decimal(str(self.prices['input_usd_per_million'])) +
                        Decimal(cap_output)*Decimal(str(self.prices['output_usd_per_million'])))/1_000_000)
        self.ledger.reserve(call_id, maximum)
        payload = {'model':self.model_id,'messages':wire_messages(messages),'max_completion_tokens':cap_output,
                   'temperature':self.configuration['temperature'],
                   'reasoning_effort':self.configuration['reasoning_effort'],'store':False,'stream':False,
                   'response_format':self.RESPONSE_FORMAT}
        write_once(self.response_dir/(digest(call_id)+'.request.json'),
                   {'call_id':call_id,'payload':payload,'input_upper_bound':upper_input})
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
            fatal=error.code in {401,403,404} or code=='insufficient_quota'  # 400 is request-specific (main r3 stop); access/config errors block all
            try:
                self._record_limits(limits,code,fatal)
            except sqlite3.OperationalError as lock_error:
                # advisory pacing state; the error artifact and ledger status below still follow
                write_once(self.response_dir/(digest(call_id)+'.limits-error.json'),
                           {'call_id':call_id,'error':type(lock_error).__name__,'path':'http_error'})
            self.ledger.unknown(call_id,'uncertain_http_'+str(error.code))
            write_once(self.response_dir/(digest(call_id)+'.error.json'),{'call_id':call_id,'http_status':error.code,
                       'error_code':code,'headers':limits,'fatal':fatal,'implicit_retries':0})
            raise ModelError('HTTP request failed; sanitized error artifact retained') from None
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            # A transport failure may still have consumed billable computation.
            self.ledger.unknown(call_id, 'uncertain_transport_error')
            raise ModelError(type(error).__name__) from None
        except BaseException:
            self.ledger.unknown(call_id, 'uncertain_interrupted_or_invalid_response')
            raise
        # Preserve provider output before parsing/settling; never persist headers/key.
        write_once(self.response_dir/(digest(call_id)+'.json'),
                   {'call_id':call_id,'request_id':request_id,'rate_limits':limits,'response':data})
        try:
            # Pacing state is advisory; a shared-ledger lock here must not abort a paid, retained call.
            self._record_limits(limits)
        except sqlite3.OperationalError as error:
            write_once(self.response_dir/(digest(call_id)+'.limits-error.json'),
                       {'call_id':call_id,'error':type(error).__name__})
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
            choice = data['choices'][0]
            if choice.get('finish_reason') != 'stop' or choice['message'].get('refusal'):
                # Settled and retained above; classified by the caller as model behaviour.
                write_once(self.response_dir/(digest(call_id)+'.incomplete.json'),
                           {'call_id':call_id,'finish_reason':choice.get('finish_reason'),
                            'refusal':bool(choice['message'].get('refusal')),'completion_tokens':o})
                raise ModelOutputIncomplete('Incomplete or refused output')
            content = choice['message']['content']
            if not isinstance(content,str):raise ValueError('Missing model output')
            operation = self.decode_content(content)
            write_once(self.response_dir/(digest(call_id)+'.operation.json'),
                       {'call_id':call_id,'raw_content':content,'operation':operation})
            return Reply(canonical(operation),i,o,actual,request_id,self.model_id)
        except (BudgetStop, ModelOutputIncomplete):raise
        except (KeyError,IndexError,TypeError,ValueError):
            self.ledger.unknown(call_id,'uncertain_invalid_response')
            raise ModelError('Invalid provider response; raw response retained') from None

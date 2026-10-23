"""Fixed prompt text. SYSTEM is the reviewed operation-runner system prompt, unchanged."""
SYSTEM = ('Work in /work to implement the supplied repository task.\n'
          'Reply with exactly one operation object in this envelope:\n'
          '{"operation":{"action":"shell","command":"..."}},\n'
          '{"operation":{"action":"test"}}, or {"operation":{"action":"done"}}.\n'
          'Shell commands can read and edit repository files. The test action runs the\n'
          'supplied test command. Tests are read-only. Network access is unavailable.\n'
          'You have at most eight responses. Do not infer success from missing output\n'
          'or incomplete test execution.')

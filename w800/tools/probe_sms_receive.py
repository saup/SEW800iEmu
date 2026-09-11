"""Read original local SMS service readiness without injecting a message."""
import argparse
import json
from pathlib import Path

from w800.guest_ui import OriginalUISession


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path,
                        default=Path('w800/reports/sms-receive-readiness.json'))
    args = parser.parse_args()
    result = {}
    with OriginalUISession(default_theme=False, audio_output='none') as session:
        session.advance(120, ('select', True))
        session.advance(1000, ('select', False))
        result['message_pid'] = hex(session.word(0x4c0b9048))
        result['message_context'] = session.d.read(0x4c0468d8, 64).hex()
        manager = session.word(0x4c04a480)
        result['manager'] = {'pointer': hex(manager), 'data':
            session.d.read(manager, 32).hex() if 0x4c000000 <= manager < 0x4c7fffe0 else None}
        result['short_message_list'] = hex(session.word(0x4c087508))
        result['storage_context'] = session.d.read(0x4c087514, 32).hex()
        # This original query only searches the request allowlist for the
        # current native MSG state. It allocates or changes nothing.
        result['tpdu_deliver_allowed'] = session.invoke(0x448ba260, 0x5741)
        result['session'] = session.report()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'session'}, indent=2))


if __name__ == '__main__':
    main()

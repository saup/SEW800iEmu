"""Use the clean movie sampler, allowing native GUI work after genuine EOF."""
from w800.tools import probe_video_realtime


class Session(probe_video_realtime.OriginalUISession):
    def start(self):
        result = super().start()
        self.saw_decoder_release = False
        self.settled_decoder_release = False
        original_diagnostics = self.q.diagnostics

        def diagnostics():
            result = original_diagnostics()
            self.saw_decoder_release |= 'AAC_RESOURCE operation=4 capacity=0' in result
            return result

        self.q.diagnostics = diagnostics
        return result

    def frame(self):
        if self.saw_decoder_release and not self.settled_decoder_release:
            # Resource release precedes its native reply and subsequent GUI
            # event. Let those original events finish before observing the
            # automatic screen transition; inject no Back/Stop operation.
            self.settled_decoder_release = True
            self.advance(1000)
        return super().frame()

    def report(self):
        result = super().report()
        result['native_eof_settle_ms'] = 1000 if self.settled_decoder_release else 0
        return result


if __name__ == '__main__':
    probe_video_realtime.OriginalUISession = Session
    probe_video_realtime.main()

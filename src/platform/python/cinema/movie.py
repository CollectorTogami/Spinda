from mgba.image import Image
from collections import namedtuple
from . import VideoFrame

Output = namedtuple('Output', ['video'])


class Tracer(object):
    def __init__(self, core):
        self.core = core
        self._video_fifo = []

    @staticmethod
    def _parse_input_schedule(input_spec):
        if not input_spec:
            return {}
        schedule = {}
        for item in str(input_spec).split(','):
            frame_text, raw_text = item.split(':', 1)
            schedule[int(frame_text)] = int(raw_text, 16)
        return schedule

    def yield_frames(self, skip=0, limit=None, input=None):
        self.framebuffer = Image(*self.core.desired_video_dimensions())
        self.core.set_video_buffer(self.framebuffer)
        self.core.reset()
        input_schedule = self._parse_input_schedule(input)
        current_keys = 0
        output_index = 0

        def install_keys(target_output_index):
            nonlocal current_keys
            if target_output_index in input_schedule:
                current_keys = input_schedule[target_output_index]
            self.core.set_keys(raw=current_keys)

        skip = (skip or 0) + 1
        while skip > 0:
            frame = self.core.frame_counter
            self.framebuffer = Image(*self.core.desired_video_dimensions())
            self.core.set_video_buffer(self.framebuffer)
            if skip == 1:
                # The first yielded frame is produced by the last pre-roll run.
                install_keys(0)
            else:
                self.core.set_keys(raw=0)
            self.core.run_frame()
            skip -= 1
        while frame <= self.core.frame_counter and limit != 0:
            self._video_fifo.append(VideoFrame(self.framebuffer.to_pil()))
            yield frame
            frame = self.core.frame_counter
            output_index += 1
            install_keys(output_index)
            self.core.run_frame()
            if limit is not None:
                assert limit >= 0
                limit -= 1

    def video(self, generator=None, **kwargs):
        if not generator:
            generator = self.yield_frames(**kwargs)
        try:
            while True:
                if self._video_fifo:
                    yield self._video_fifo.pop(0)
                else:
                    next(generator)
        except StopIteration:
            return

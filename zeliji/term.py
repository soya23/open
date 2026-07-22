"""Minimal cross-platform terminal layer. No curses.

windows-curses (PDCurses) miscounts East-Asian wide characters, which
smeared the cockpit on real Windows terminals. We own the cell math
(cockpit wraps/pads by cells), so all the terminal has to do is print
UTF-8 and honor VT escape codes — which Windows Terminal, conhost
(Win10+) and every Unix terminal do.
"""

from __future__ import annotations

import os
import shutil
import sys
import time

IS_WIN = os.name == "nt"
if IS_WIN:
    import ctypes
    import msvcrt
else:
    import codecs
    import select
    import termios
    import tty

LEFT, RIGHT = "KEY_LEFT", "KEY_RIGHT"
_WIN_KEYS = {"K": LEFT, "M": RIGHT}


class Term:
    def __enter__(self) -> "Term":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if IS_WIN:
            kernel32 = ctypes.windll.kernel32
            for std in (-11, -10):  # stdout, stdin: enable VT processing
                handle = kernel32.GetStdHandle(std)
                mode = ctypes.c_uint32()
                if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                    kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        else:
            self._fd = sys.stdin.fileno()
            self._old = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)
            self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        sys.stdout.write("\x1b[?1049h\x1b[?25l\x1b[2J")  # alt screen, hide cursor
        sys.stdout.flush()
        return self

    def __exit__(self, *exc) -> None:
        sys.stdout.write("\x1b[0m\x1b[?25h\x1b[?1049l")
        sys.stdout.flush()
        if not IS_WIN:
            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old)

    @staticmethod
    def size() -> tuple[int, int]:
        s = shutil.get_terminal_size()
        return s.columns, s.lines

    def draw(self, lines: list[str]) -> None:
        """Repaint the whole frame: overwrite in place, no flicker."""
        _, h = self.size()
        buf = "\x1b[H"
        for line in lines[:h]:
            buf += line + "\x1b[0m\x1b[K\x1b[1E"  # reset, clear rest, next line
        buf += "\x1b[0m\x1b[J"  # clear anything below
        sys.stdout.write(buf)
        sys.stdout.flush()

    # ------------------------------------------------------------ input

    def read_keys(self, timeout: float) -> list[str]:
        """Keys pressed within `timeout`: printable chars (str), control
        chars ("\\t", "\\r", "\\x1b", "\\x7f"/"\\x08"), or KEY_LEFT/KEY_RIGHT."""
        if IS_WIN:
            return self._read_win(timeout)
        return self._read_unix(timeout)

    @staticmethod
    def _read_win(timeout: float) -> list[str]:
        keys: list[str] = []
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            while msvcrt.kbhit():
                ch = msvcrt.getwch()
                if ch in ("\x00", "\xe0"):  # function/arrow prefix
                    code = msvcrt.getwch()
                    if code in _WIN_KEYS:
                        keys.append(_WIN_KEYS[code])
                else:
                    keys.append(ch)
            if keys:
                return keys
            time.sleep(0.02)
        return keys

    def _read_unix(self, timeout: float) -> list[str]:
        ready, _, _ = select.select([self._fd], [], [], timeout)
        if not ready:
            return []
        data = os.read(self._fd, 1024)
        keys: list[str] = []
        i = 0
        while i < len(data):
            if data[i] == 0x1B and data[i + 1:i + 2] == b"[":
                final = data[i + 2:i + 3]
                if final == b"C":
                    keys.append(RIGHT)
                elif final == b"D":
                    keys.append(LEFT)
                i += 3  # swallow other CSI sequences too
                continue
            j = i + 1
            while j < len(data) and data[j] >= 0x80:  # utf-8 continuation
                j += 1
            keys.extend(self._decoder.decode(data[i:j]))
            i = j
        return keys

"""웹 화면에서 오래 걸리는 작업(글쓰기, 에디터 입력, 학습)을 백그라운드로 돌린다.

- 한 번에 하나만 돈다 (브라우저·사진 기록이 섞이지 않도록).
- 작업 중 print 출력은 화면 로그로 보여준다.
- 작업이 사람의 답을 기다려야 하면(발행했나요? 등) 질문을 띄우고 버튼 응답을 기다린다.
"""

from __future__ import annotations

import io
import itertools
import threading
import traceback
from contextlib import redirect_stdout
from datetime import datetime

from blogbot import publisher

_ids = itertools.count(1)
_lock = threading.Lock()
JOBS: dict[int, "Job"] = {}


class Busy(Exception):
    pass


class _LogWriter(io.TextIOBase):
    def __init__(self, job: "Job"):
        self.job = job

    def write(self, s: str) -> int:
        for line in s.splitlines():
            if line.strip():
                self.job.log.append(line)
        return len(s)


class Job:
    def __init__(self, kind: str, title: str):
        self.id = next(_ids)
        self.kind = kind
        self.title = title
        self.status = "running"  # running / waiting / done / error
        self.log: list[str] = []
        self.question: dict | None = None
        self.result = None
        self.error = ""
        self.started_at = datetime.now()
        self._answer: str | None = None
        self._answered = threading.Event()

    # publisher.ask 대신 쓰인다: 질문을 화면에 띄우고 버튼 응답을 기다린다
    def ask(self, message: str, choices: dict[str, str] | None = None) -> str:
        self.question = {"message": message, "choices": choices or {"": "확인"}}
        self.status = "waiting"
        self._answered.clear()
        self._answered.wait()
        self.question = None
        self.status = "running"
        return self._answer or ""

    def answer(self, value: str) -> None:
        if self.question is not None:
            self._answer = value
            self._answered.set()

    def to_dict(self) -> dict:
        return {
            "id": self.id, "kind": self.kind, "title": self.title, "status": self.status,
            "log": self.log[-200:], "question": self.question, "result": self.result, "error": self.error,
        }


def active() -> Job | None:
    for job in JOBS.values():
        if job.status in ("running", "waiting"):
            return job
    return None


def start(kind: str, title: str, fn) -> Job:
    with _lock:
        running = active()
        if running:
            raise Busy(f"'{running.title}' 작업이 아직 진행 중입니다.")
        job = Job(kind, title)
        JOBS[job.id] = job

    def run():
        publisher.ask = job.ask
        try:
            with redirect_stdout(_LogWriter(job)):
                job.result = fn()
            job.status = "done"
        except SystemExit as e:  # 프로그램이 일부러 멈춘 경우: 메시지가 곧 안내문
            job.error = str(e.code) if e.code not in (None, 0) else ""
            job.status = "error" if job.error else "done"
        except Exception as e:
            job.error = f"{type(e).__name__}: {e}"
            job.log.extend(traceback.format_exc().splitlines()[-6:])
            job.status = "error"
        finally:
            publisher.ask = publisher._terminal_ask

    threading.Thread(target=run, daemon=True).start()
    return job

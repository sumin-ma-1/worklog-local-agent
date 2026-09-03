# worklog-local-agent

텔레그램 채팅방 활동을 모아 날짜별 업무 일지를 만드는 로컬 에이전트입니다.

```
Telethon 수집 → 첨부파일 아카이브 → 날짜별 정리 → 업무 일지 생성
```

사용자 계정(Telethon)으로 기존 업무 채팅방을 읽습니다. 봇이 아니라 **본인 계정 세션**을 사용합니다.

## 파이프라인

1. **collect** — 지정한 채팅방 메시지를 증분 수집합니다.
2. **archive** — 사진·문서·영상 등 첨부파일을 날짜/채팅방별로 저장합니다.
3. **organize** — `Asia/Seoul` 날짜 기준으로 하루치 메시지를 묶습니다.
4. **journal** — 로컬 Ollama 모델로 업무 일지를 작성합니다.

## 저장 위치

```
data/
  sessions/          Telethon 세션
  raw/<chat_id>/     원본 메시지 JSONL
  attachments/<날짜>/<채팅방>/
  daily/<날짜>.json  날짜별 정리본
  journals/<날짜>.md 업무 일지
```

## 시작하기

텔레그램 API는 [my.telegram.org](https://my.telegram.org)에서 발급합니다.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env
cp config.example.yaml config.yaml
```

`.env`에 `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`를 넣고, `config.yaml`의 `telegram.chats`에 채팅방 제목 또는 ID를 적습니다.

```bash
worklog-agent auth          # 전화번호·인증코드 로그인
worklog-agent chats         # 참여 중인 대화 목록
worklog-agent run           # 수집부터 일지까지
worklog-agent run --date 2026-09-02
```

단계별로 실행할 수도 있습니다.

```bash
worklog-agent collect
worklog-agent archive
worklog-agent organize --date 2026-09-02
worklog-agent journal --date 2026-09-02
```

일지 생성은 로컬 Ollama를 사용합니다. 기본 모델은 `gemma4:e4b`입니다.

```bash
ollama serve
ollama pull gemma4:e4b
```

모델은 `config.yaml`의 `journal.ollama.model` 또는 `OLLAMA_MODEL`로 바꿉니다.

## 요구 사항

- Python 3.10+
- 텔레그램 계정과 API ID/Hash
- 로컬 Ollama (`ollama serve`)

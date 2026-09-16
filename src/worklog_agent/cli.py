from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import typer

from worklog_agent.config import AppConfig, load_config
from worklog_agent.pipeline import Pipeline

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="텔레그램 채팅 활동을 모아 날짜별 업무 일지를 만듭니다.",
)


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )


def _pipeline(ctx: typer.Context) -> Pipeline:
    return Pipeline(ctx.obj)


@app.callback()
def main(
    ctx: typer.Context,
    config: Path = typer.Option(Path("config.yaml"), "--config", help="설정 파일 경로"),
) -> None:
    _setup_logging()
    ctx.obj = load_config(config)


@app.command()
def auth(ctx: typer.Context) -> None:
    """Telethon 세션을 만들고 텔레그램에 로그인합니다."""
    asyncio.run(_auth(ctx.obj))


async def _auth(config: AppConfig) -> None:
    from worklog_agent.collect import build_client, ensure_authorized
    from worklog_agent.storage import Storage

    storage = Storage(config.data_root)
    storage.ensure()
    client = build_client(config, storage)
    async with client:
        await ensure_authorized(client, config)
        me = await client.get_me()
        name = " ".join(part for part in [me.first_name, me.last_name] if part)
        typer.echo(f"로그인 완료: {name} (id={me.id})")


@app.command()
def chats(ctx: typer.Context) -> None:
    """참여 중인 대화 목록을 출력합니다. config.yaml 의 chats 값으로 쓰세요."""
    asyncio.run(_chats(ctx.obj))


async def _chats(config: AppConfig) -> None:
    from worklog_agent.collect import load_dialogs

    rows = await load_dialogs(config, interactive=True)
    if not rows:
        typer.echo("대화가 없습니다.")
        return
    for row in rows:
        typer.echo(f"{row['id']}\t{row['type']}\t{row['title']}")


@app.command()
def collect(
    ctx: typer.Context,
    date: str | None = typer.Option(None, "--date", help="YYYY-MM-DD, 기본값 오늘"),
) -> None:
    """지정한 날짜의 채팅방 메시지를 수집합니다."""
    count = asyncio.run(_pipeline(ctx).collect(date))
    typer.echo(f"새 메시지 {count}개 수집")


@app.command()
def archive(ctx: typer.Context) -> None:
    """아직 받지 않은 첨부파일을 다운로드합니다."""
    count = asyncio.run(_pipeline(ctx).archive())
    typer.echo(f"첨부파일 {count}개 저장")


@app.command("organize")
def organize_cmd(
    ctx: typer.Context,
    date: str | None = typer.Option(None, "--date", help="YYYY-MM-DD, 기본값 오늘"),
) -> None:
    """메시지를 날짜별로 묶습니다."""
    bundle = _pipeline(ctx).organize(date)
    typer.echo(
        f"{bundle.date} 정리 완료 — 채팅 {bundle.totals.get('chats', 0)}, "
        f"메시지 {bundle.totals.get('messages', 0)}, 첨부 {bundle.totals.get('attachments', 0)}"
    )


@app.command()
def journal(
    ctx: typer.Context,
    date: str | None = typer.Option(None, "--date", help="YYYY-MM-DD, 기본값 오늘"),
) -> None:
    """날짜별 정리본으로 업무 일지를 생성합니다."""
    path = asyncio.run(_pipeline(ctx).journal(date))
    typer.echo(f"일지 저장: {path}")


@app.command()
def run(
    ctx: typer.Context,
    date: str | None = typer.Option(None, "--date", help="YYYY-MM-DD, 기본값 오늘"),
) -> None:
    """수집 → 아카이브 → 정리 → 일지 생성을 한 번에 실행합니다."""
    path = asyncio.run(_pipeline(ctx).run(date))
    typer.echo(f"완료: {path}")


@app.command("schedule-tick")
def schedule_tick(ctx: typer.Context) -> None:
    """예약된 일지 생성을 실행합니다. cron/systemd 에서 1분마다 호출하세요."""
    from worklog_agent.config import AppConfig
    from worklog_agent.schedules import tick_all_schedules
    from worklog_agent.web.run_service import RuntimeRegistry

    config: AppConfig = ctx.obj
    registry = RuntimeRegistry()
    results = tick_all_schedules(config, registry.runtime_for, wait=True)
    if not results:
        typer.echo("실행할 예약이 없습니다.")
        return
    for row in results:
        typer.echo(f"{row['user_id']}\t{row['schedule_id']}\t{row['status']}")


@app.command()
def dashboard(
    ctx: typer.Context,
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8787, "--port"),
) -> None:
    """로컬 웹 대시보드를 엽니다."""
    import uvicorn

    from worklog_agent.web.app import create_app

    config: AppConfig = ctx.obj
    typer.echo(f"대시보드: http://{host}:{port}")
    uvicorn.run(create_app(config.config_path), host=host, port=port, log_level="info")

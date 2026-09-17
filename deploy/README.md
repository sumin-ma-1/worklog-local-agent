# Worklog 대시보드 공개 (고정 주소)

## 0) 개발용 quick tunnel (먼저 써보기)

고정 도메인 없이 `*.trycloudflare.com` 임시 HTTPS로 바로 열 수 있습니다.

```bash
systemctl --user enable --now cloudflared-worklog-quick.service
# URL은 로그에 출력됩니다 (재시작마다 바뀔 수 있음)
rg -o 'https://[a-zA-Z0-9.-]+\.trycloudflare\.com' ~/.cloudflared/worklog-quick-tunnel.log | tail -1
```

`.env`의 `DASHBOARD_URL`을 그 URL로 맞춘 뒤 대시보드를 재시작하세요.

괜찮으면 아래 **고정 도메인 Tunnel**로 옮기면 됩니다.


```bash
mkdir -p ~/.config/systemd/user ~/worklog-local-agent/data
ln -sfn /mnt/data/sumin/worklog-local-agent ~/worklog-local-agent
cp /mnt/data/sumin/worklog-local-agent/deploy/worklog-dashboard.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now worklog-dashboard.service
systemctl --user status worklog-dashboard.service
```

로그:

```bash
tail -f /mnt/data/sumin/worklog-local-agent/data/dashboard.log
```

재시작:

```bash
systemctl --user restart worklog-dashboard.service
```

`loginctl` linger가 이미 `yes`라 로그아웃/SSH 종료 후에도 유지됩니다.

## 2) 고정 HTTPS 도메인 (Cloudflare Tunnel)

1. [cloudflared 설치](https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/install-and-setup/installation/)
2. Cloudflare에 도메인이 있어야 합니다 (또는 무료 계정 + 도메인 추가).
3. 아래를 한 번만:

```bash
cloudflared tunnel login
cloudflared tunnel create worklog
# 출력된 Tunnel ID를 deploy/cloudflared-config.yml 의 <TUNNEL_ID>에 넣기
cloudflared tunnel route dns worklog worklog.YOUR_DOMAIN
```

4. `~/.cloudflared/config.yml` 에 `deploy/cloudflared-config.yml` 내용을 넣고 경로를 맞춘 뒤:

```bash
cp /mnt/data/sumin/worklog-local-agent/deploy/cloudflared-worklog.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now cloudflared-worklog.service
```

5. `.env`에:

```bash
DASHBOARD_URL=https://worklog.YOUR_DOMAIN
```

6. 대시보드 재시작:

```bash
systemctl --user restart worklog-dashboard.service
```

텔레그램 「대시보드」 메뉴 버튼은 **HTTPS** URL일 때 WebApp으로 붙습니다.

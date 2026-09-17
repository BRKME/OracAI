#!/usr/bin/env bash
# Дёргает workflow_dispatch у воркфлоу OracAI.
#
# Зачем: scheduled-воркфлоу на free-tier GitHub запускаются когда придётся —
# замерены задержки 2-114 мин и разрывы до 13.4 часа. Прогоны, запущенные
# через workflow_dispatch, стартуют за секунды и под эту раздачу не попадают.
# Поэтому время задаёт VPS, а GitHub только выполняет.
#
# Использование: dispatch.sh <файл-воркфлоу> [force]
#   dispatch.sh lp_system.yml true    — отчёт по расписанию (шлём принудительно)
#   dispatch.sh lp_system.yml         — проверка диапазона (молчит, если всё ок)
#   dispatch.sh regime_check.yml      — воркфлоу без входного параметра force

set -euo pipefail

WORKFLOW="${1:?укажи файл воркфлоу, например lp_system.yml}"
FORCE="${2:-false}"
REPO="${GH_REPO:-BRKME/OracAI}"
REF="${GH_REF:-main}"
ENV_FILE="/etc/oracai-dispatch.env"

# shellcheck source=/dev/null
[[ -f "$ENV_FILE" ]] && source "$ENV_FILE"

: "${GH_TOKEN:?GH_TOKEN не задан — проверь $ENV_FILE}"

# inputs передаём ТОЛЬКО когда просят force: воркфлоу, который не объявил
# такой параметр (regime_check.yml), ответит 422 на лишний ключ.
if [[ "$FORCE" == "true" ]]; then
    PAYLOAD="{\"ref\":\"${REF}\",\"inputs\":{\"force\":\"true\"}}"
else
    PAYLOAD="{\"ref\":\"${REF}\"}"
fi

log() { echo "$(date '+%Y-%m-%d %H:%M:%S %Z') [$WORKFLOW force=$FORCE] $*"; }

# GitHub изредка отдаёт 5xx — три попытки с паузой
for attempt in 1 2 3; do
    code=$(curl -sS -o /tmp/dispatch_resp.txt -w '%{http_code}' \
        -X POST \
        -H "Accept: application/vnd.github+json" \
        -H "Authorization: Bearer ${GH_TOKEN}" \
        -H "X-GitHub-Api-Version: 2022-11-28" \
        "https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches" \
        -d "$PAYLOAD" || echo 000)

    if [[ "$code" == "204" ]]; then
        log "запущен (попытка $attempt)"
        exit 0
    fi

    log "HTTP $code: $(head -c 200 /tmp/dispatch_resp.txt)"
    # 401/403/404 повторять бессмысленно — это токен или права
    case "$code" in
        401|403|404) log "не повторяем, чиним токен/права"; exit 1 ;;
    esac
    sleep $((attempt * 10))
done

log "не удалось запустить за 3 попытки"
exit 1

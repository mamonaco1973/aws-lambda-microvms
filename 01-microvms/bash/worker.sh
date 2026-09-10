#!/bin/bash
# ==============================================================================
# A real, persistent bash shell holding one session's state.
#
# The bash counterpart of worker.py and worker.js, deliberately structured the
# same way so all three can be read side by side. Its variables, arrays,
# functions, working directory and background jobs survive suspend and resume
# because AWS checkpoints the VM's memory -- there is no save path, no
# serialization and no replay. When a resumed session still knows `balance`,
# it is the same shell that set it.
#
# The VM is the security boundary, not this shell. Submitted code runs through
# eval with full access to the process, which is acceptable only because the
# MicroVM around it is isolated and holds no AWS credentials.
#
# NOT `set -euo pipefail`, and that is deliberate -- it is the one place in this
# project where the house rule is wrong. Under `-e` a single failing cell would
# kill the shell and take the whole session with it, so a failed command has to
# be reported as data instead. `-u` and `-o pipefail` are omitted for the same
# reason: they would silently change the semantics of submitted code.
# ==============================================================================

# Every internal name is prefixed. Submitted code shares this shell, so an
# unprefixed `line` or `rc` would be clobbered by an ordinary-looking cell and
# break the protocol from the inside.
_mv_scratch=$(mktemp -d)
_mv_raw="${_mv_scratch}/cell.raw"
_mv_capped="${_mv_scratch}/cell.out"
trap 'rm -rf "${_mv_scratch}"' EXIT

# Matches the Python and Node workers, so a runaway loop cannot return a
# response too large for API Gateway.
_mv_output_limit=16000

# ------------------------------------------------------------------------------
# Millisecond clock — sets _mv_ms
# ------------------------------------------------------------------------------
# Sets a global rather than echoing, because $(...) forks a subshell. That is
# the trap this whole file is arranged to avoid; see the eval below.
# 10# forces base 10: microseconds like 012345 would otherwise parse as octal.
_mv_now_ms() {
  local _t=${EPOCHREALTIME/,/.}
  _mv_ms=$(( ${_t%.*} * 1000 + 10#${_t#*.} / 1000 ))
}

# ------------------------------------------------------------------------------
# Initialization — the work the snapshot is taken around
# ------------------------------------------------------------------------------
# Runs before the readiness line is printed, so the image build blocks here and
# every launched MicroVM restores with this already in memory. Bash takes a few
# seconds to build the array where Python and Node take milliseconds, which
# makes this the one runtime where the snapshot's saving is visible on a clock.
#
# Values are held in integer cents. The other two workers divide by 100 for a
# float; bash has no floating point, so the dataset digest differs by design and
# is never compared across runtimes.
# ------------------------------------------------------------------------------
_mv_now_ms; _mv_started=${_mv_ms}

_mv_rows=200000
declare -a sales
declare -A totals
# Associative subscripts are strings, not arithmetic: totals[_mv_m] would
# key on the literal name and quietly collapse all twelve months into one.
for ((_mv_m = 0; _mv_m < 12; _mv_m++)); do totals["${_mv_m}"]=0; done
for ((_mv_i = 0; _mv_i < _mv_rows; _mv_i++)); do
  _mv_month=$((_mv_i % 12))
  _mv_value=$(((_mv_i * 7919) % 10000))
  sales[_mv_i]="${_mv_month} ${_mv_value}"
  totals["${_mv_month}"]=$((totals["${_mv_month}"] + _mv_value))
done

_mv_now_ms; _mv_init_ms=$((_mv_ms - _mv_started))

_mv_digest=$(for _mv_m in {0..11}; do
  printf '%s=%s;' "${_mv_m}" "${totals["${_mv_m}"]}"
done | sha256sum | cut -d' ' -f1)

jq -nc --argjson rows "${_mv_rows}" --argjson init "${_mv_init_ms}" \
  --arg digest "${_mv_digest}" --argjson pid "$$" \
  '{ready: true, rows: $rows, init_ms: $init, dataset_sha256: $digest, pid: $pid}'

# ------------------------------------------------------------------------------
# Cell loop — one JSON object per line in each direction
# ------------------------------------------------------------------------------
while IFS= read -r _mv_line; do
  if ! _mv_code=$(jq -re '.code' <<<"${_mv_line}" 2>/dev/null); then
    # Answer anyway. A silent iteration would hang the server waiting on a
    # response that never arrives.
    jq -nc '{ok: false, stdout: "Malformed request line."}'
    continue
  fi

  _mv_now_ms; _mv_started=${_mv_ms}

  # The single most important line in this file. `out=$(eval ...)` would run the
  # cell in a subshell, so `balance=41` would vanish the moment it returned and
  # the entire demonstration would silently persist nothing. Redirecting to a
  # file keeps eval in THIS shell, which is what makes state survive.
  #
  # </dev/null matters just as much: stdin is the protocol channel, so a cell
  # calling `read` or `cat` would otherwise swallow the next request line.
  eval "${_mv_code}" >"${_mv_raw}" 2>&1 </dev/null
  _mv_rc=$?

  _mv_now_ms; _mv_elapsed=$((_mv_ms - _mv_started))

  # NULs cannot appear in JSON and would abort the encoder mid-response.
  head -c "${_mv_output_limit}" "${_mv_raw}" | tr -d '\000' > "${_mv_capped}"

  if [[ ${_mv_rc} -eq 0 ]]; then _mv_ok=true; else _mv_ok=false; fi

  jq -nc --rawfile stdout "${_mv_capped}" --argjson ok "${_mv_ok}" \
    --argjson ms "${_mv_elapsed}" \
    '{ok: $ok, stdout: $stdout, execution_ms: $ms}' \
    || jq -nc '{ok: false, stdout: "Cell output could not be encoded as JSON."}'
done

# Reaching here means stdin closed: the server is gone, so the shell should be
# too. A cell calling `exit` also lands here, which is the documented way this
# runtime loses a session -- bash has no equivalent of catching SystemExit.

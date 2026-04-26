#!/usr/bin/env bash
# iter28 Phase A: pivot_bounce single-strategy session sweep.
# For each (pivot_period, session) pair, run quick_eval and grep
# the validation summary line.
set -e
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"

mkdir -p config/iter28/_phaseA
TMPL='extends: ../../default.yaml
instrument:
  symbol: XAUUSD
  timeframe: M1
  contract_size: 100
  tick_size: 0.01
  tick_value: 1.0
  quote_currency: USD
risk:
  risk_per_trade_pct: 7.0
  daily_profit_target_pct: 30.0
  daily_max_loss_pct: 3.0
  withdraw_half_of_daily_profit: false
  max_concurrent_positions: 1
  lot_cap_per_unit_balance: 0.000020
  dynamic_risk_enabled: true
  min_risk_per_trade_pct: 1.0
  max_risk_per_trade_pct: 7.0
  drawdown_soft_limit_pct: 12.0
  drawdown_hard_limit_pct: 22.0
  drawdown_soft_multiplier: 0.7
  drawdown_hard_multiplier: 0.4
strategy:
  __replace__: true
  name: pivot_bounce
  params:
    pivot_period: __PERIOD__
    atr_period: 14
    touch_atr_buf: __TOUCH__
    sl_atr_buf: __SLBUF__
    max_sl_atr: __MAXSL__
    tp1_rr: __TP1__
    tp2_rr: __TP2__
    leg1_weight: 0.5
    cooldown_bars: __COOLDOWN__
    session: __SESSION__
    use_s2r2: true
    max_trades_per_day: __MAXT__
'

mk(){
  local period="$1" sess="$2"
  case "$period" in
    daily)   touch=0.05; slbuf=0.30; maxsl=2.0; tp1=1.0; tp2=1.5; cool=60;  maxt=4 ;;
    weekly)  touch=0.10; slbuf=0.28; maxsl=2.0; tp1=1.0; tp2=2.5; cool=60;  maxt=2 ;;
    monthly) touch=0.15; slbuf=0.30; maxsl=3.0; tp1=1.2; tp2=2.5; cool=120; maxt=1 ;;
  esac
  local fp="config/iter28/_phaseA/${period}_${sess}.yaml"
  echo "$TMPL" \
    | sed "s|__PERIOD__|$period|; s|__SESSION__|$sess|; s|__TOUCH__|$touch|; s|__SLBUF__|$slbuf|; s|__MAXSL__|$maxsl|; s|__TP1__|$tp1|; s|__TP2__|$tp2|; s|__COOLDOWN__|$cool|; s|__MAXT__|$maxt|" \
    > "$fp"
  echo "$fp"
}

printf "%-8s %-14s | %-32s | %-32s | %-24s\n" "period" "session" "FULL ret/PF/DD" "VAL ret/PF/DD" "TOURN ret/PF"
echo "---------------------------------------------------------------------------------------------------------------------"
for period in daily weekly monthly; do
  for sess in london ny overlap london_or_ny; do
    fp=$(mk "$period" "$sess")
    out=$(python3 scripts/quick_eval.py --config "$fp" --csv data/xauusd_m1_2026.csv 2>&1)
    full=$(echo "$out" | awk '/^== FULL ==/{getline; print}')
    val=$(echo "$out"  | awk '/^== VALIDATION /{getline; print}')
    tourn=$(echo "$out" | awk '/^== TOURNAMENT 14d/{getline; print}')
    fr=$(echo "$full"  | grep -oE 'return_pct=[^ ]+'   | cut -d= -f2)
    fp_=$(echo "$full" | grep -oE 'profit_factor=[^ ]+' | cut -d= -f2)
    fd=$(echo "$full"  | grep -oE 'max_drawdown_pct=[^ ]+' | cut -d= -f2)
    vr=$(echo "$val"   | grep -oE 'return_pct=[^ ]+'   | cut -d= -f2)
    vp=$(echo "$val"   | grep -oE 'profit_factor=[^ ]+' | cut -d= -f2)
    vd=$(echo "$val"   | grep -oE 'max_drawdown_pct=[^ ]+' | cut -d= -f2)
    tr=$(echo "$tourn" | grep -oE 'return_pct=[^ ]+'   | cut -d= -f2)
    tp=$(echo "$tourn" | grep -oE 'profit_factor=[^ ]+' | cut -d= -f2)
    printf "%-8s %-14s | %8s/%5s/%8s          | %8s/%5s/%8s          | %8s/%5s\n" \
      "$period" "$sess" "${fr:-NA}" "${fp_:-NA}" "${fd:-NA}" "${vr:-NA}" "${vp:-NA}" "${vd:-NA}" "${tr:-NA}" "${tp:-NA}"
  done
done

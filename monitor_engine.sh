#!/bin/bash
# Automatic Engine Monitoring Script
# Watches logs and alerts on key events

LOG_FILE="logs/2026-03-03/engine_2026-03-03.log"
LAST_LINE=0

echo "========================================"
echo "ENGINE MONITOR STARTED"
echo "Watching: $LOG_FILE"
echo "Time: $(TZ='America/New_York' date '+%H:%M:%S ET')"
echo "========================================"
echo ""

# Track what we've already alerted on
ALERTED_ORB_COMPLETE=0
ALERTED_SIGNAL=0
ALERTED_ENTRY=0
ALERTED_POSITION=0
ALERTED_EXIT=0

while true; do
    if [ ! -f "$LOG_FILE" ]; then
        sleep 5
        continue
    fi

    # Get new lines since last check
    TOTAL_LINES=$(wc -l < "$LOG_FILE")
    if [ $TOTAL_LINES -gt $LAST_LINE ]; then
        NEW_LINES=$(tail -n +$((LAST_LINE + 1)) "$LOG_FILE")

        # Check for ORB completion
        if [ $ALERTED_ORB_COMPLETE -eq 0 ] && echo "$NEW_LINES" | grep -q "\[ORB COMPLETE\]"; then
            echo "=========================================="
            echo "🎯 EVENT: ORB COMPLETE"
            echo "Time: $(TZ='America/New_York' date '+%H:%M:%S ET')"
            echo "=========================================="
            echo "$NEW_LINES" | grep -A 5 "\[ORB COMPLETE\]"
            echo ""
            ALERTED_ORB_COMPLETE=1
        fi

        # Check for signal generation
        if [ $ALERTED_SIGNAL -eq 0 ] && echo "$NEW_LINES" | grep -q "\[SIGNAL\]"; then
            echo "=========================================="
            echo "📊 EVENT: SIGNAL GENERATED"
            echo "Time: $(TZ='America/New_York' date '+%H:%M:%S ET')"
            echo "=========================================="
            echo "$NEW_LINES" | grep -A 8 "\[SIGNAL\]"
            echo ""
            ALERTED_SIGNAL=1
        fi

        # Check for bracket order mode
        if [ $ALERTED_ENTRY -eq 0 ] && echo "$NEW_LINES" | grep -q "\[BRACKET ORDER MODE\]"; then
            echo "=========================================="
            echo "🚀 EVENT: ENTERING POSITION (BRACKET MODE)"
            echo "Time: $(TZ='America/New_York' date '+%H:%M:%S ET')"
            echo "=========================================="
            echo "$NEW_LINES" | grep -A 3 "\[BRACKET ORDER MODE\]"
            echo ""
            ALERTED_ENTRY=1
        fi

        # Check for position opened
        if [ $ALERTED_POSITION -eq 0 ] && echo "$NEW_LINES" | grep -q "\[TRADE OPENED\]"; then
            echo "=========================================="
            echo "✅ EVENT: POSITION OPENED"
            echo "Time: $(TZ='America/New_York' date '+%H:%M:%S ET')"
            echo "=========================================="
            echo "$NEW_LINES" | grep -B 5 -A 15 "\[TRADE OPENED\]"
            echo ""
            echo "📝 State file created:"
            if [ -f "state/position_state.json" ]; then
                echo "   ✓ state/position_state.json exists"
                echo ""
                echo "🎯 Bracket Orders:"
                cat state/position_state.json | grep -E "target_order_id|sl_order_id|oca_group" || echo "   (parsing...)"
            fi
            echo ""
            ALERTED_POSITION=1
        fi

        # Check for exit
        if [ $ALERTED_EXIT -eq 0 ] && echo "$NEW_LINES" | grep -q "\[POSITION CLOSED\]"; then
            echo "=========================================="
            echo "🏁 EVENT: POSITION CLOSED"
            echo "Time: $(TZ='America/New_York' date '+%H:%M:%S ET')"
            echo "=========================================="
            echo "$NEW_LINES" | grep -B 2 -A 10 "\[POSITION CLOSED\]"
            echo ""
            ALERTED_EXIT=1
        fi

        # Check for errors
        if echo "$NEW_LINES" | grep -q "ERROR\|CRITICAL\|FAILED"; then
            echo "=========================================="
            echo "⚠️  ERROR DETECTED"
            echo "Time: $(TZ='America/New_York' date '+%H:%M:%S ET')"
            echo "=========================================="
            echo "$NEW_LINES" | grep "ERROR\|CRITICAL\|FAILED"
            echo ""
        fi

        # Check for session end
        if echo "$NEW_LINES" | grep -q "\[END-OF-DAY RECONCILIATION\]"; then
            echo "=========================================="
            echo "🌙 EVENT: END OF DAY RECONCILIATION"
            echo "Time: $(TZ='America/New_York' date '+%H:%M:%S ET')"
            echo "=========================================="
            echo "$NEW_LINES" | grep -A 20 "\[END-OF-DAY RECONCILIATION\]"
            echo ""
        fi

        LAST_LINE=$TOTAL_LINES
    fi

    # Sleep for 5 seconds between checks
    sleep 5
done

# -*- coding: utf-8 -*-
# Exit reason codes
EXIT_SL           = 1
EXIT_TP           = 2
EXIT_RCI_LEVEL    = 3
EXIT_RCI_BOUNCE   = 4
EXIT_BAND_BREAK   = 5
EXIT_MA_BREAK     = 6
EXIT_MACD_ZERO    = 7
EXIT_MACD_RETRACE = 8
EXIT_TIME         = 9
EXIT_REVERSE      = 10
EXIT_END          = 11

EXIT_CODE_TO_STR = {
    1: "SL", 2: "TP", 3: "RCI_Level", 4: "RCI_Bounce",
    5: "BandBreak", 6: "MABreak", 7: "MACD_Zero",
    8: "MACD_Retrace", 9: "TimeExit", 10: "Reverse", 11: "EndOfData",
}

# Entry reason codes
ENTRY_SELL         = 1
ENTRY_BUY          = 2
ENTRY_REVERSE_SELL = 3
ENTRY_REVERSE_BUY  = 4

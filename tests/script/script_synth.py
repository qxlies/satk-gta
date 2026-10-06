"""Synthetic opcode database and scripts for satk.script tests (no game files)."""

from __future__ import annotations

from satk.script.opdb import OpcodeDB

#: A small opcode table in the kb's row format (ids and shapes as in SA; one invented command 0111).
ROWS = [
    ("0000", "NOP", "default", "", [], []),
    ("0001", "WAIT", "default", "", ["time: int"], []),
    ("0002", "GOTO", "default", "branch", ["label"], []),
    ("0004", "SET_VAR_INT", "default", "", ["int (global var)", "int (literal)"], []),
    ("0006", "SET_LVAR_INT", "default", "", ["int (local var)", "int (literal)"], []),
    ("0007", "SET_LVAR_FLOAT", "default", "", ["float (local var)", "float (literal)"], []),
    ("000A", "ADD_VAL_TO_INT_LVAR", "default", "", ["int (local var)", "int (literal)"], []),
    ("0039", "IS_INT_LVAR_EQUAL_TO_NUMBER", "default", "condition", ["int (local var)", "int (literal)"], []),
    ("004D", "GOTO_IF_FALSE", "default", "branch", ["label"], []),
    ("004E", "TERMINATE_THIS_SCRIPT", "default", "branch", [], []),
    ("004F", "START_NEW_SCRIPT", "default", "", ["label", "arguments"], []),
    ("0050", "GOSUB", "default", "", ["label"], []),
    ("0051", "RETURN", "default", "", [], []),
    ("00A0", "GET_CHAR_COORDINATES", "default", "", ["self: Char"],
     ["x: float (variable)", "y: float (variable)", "z: float (variable)"]),
    ("00D6", "IF", "default", "", ["int"], []),
    ("0111", "TEST_ANY", "default", "", ["a: any", "b: any", "c: any", "d: any"], []),
    ("0213", "CREATE_PICKUP", "default", "", ["modelId: model_object", "pickupType: int", "x: float", "y: float",
                                              "z: float"], ["handle: Pickup (variable)"]),
    ("0256", "IS_PLAYER_PLAYING", "default", "condition", ["self: Player"], []),
    ("03A4", "SCRIPT_NAME", "default", "", ["name: string"], []),
    ("0417", "LOAD_AND_LAUNCH_MISSION_INTERNAL", "default", "", ["index: int"], []),
    ("05B6", "SAVE_STRING_TO_DEBUG_FILE", "default", "", ["msg: string128"], []),
    ("0A93", "TERMINATE_THIS_CUSTOM_SCRIPT", "CLEO", "branch", [], []),
    ("0AB1", "CLEO_CALL", "CLEO", "", ["label", "numArgs: int", "args: arguments"], []),
    ("0AB2", "CLEO_RETURN", "CLEO", "", ["numRet: int", "retParams: arguments"], []),
    ("0AB0", "IS_KEY_PRESSED", "CLEO", "condition", ["keyCode: KeyCode"], []),
    ("0AD1", "PRINT_FORMATTED_NOW", "CLEO", "", ["format: string", "time: int", "args: arguments"], []),
    ("0B20", "READ_CLIPBOARD_DATA", "CLEO+", "", ["memory: int", "size: int"], []),
    ("0B20", "SAMP_GET_PLAYER_CHAR_BY_ID", "SAMPFUNCS", "", ["id: int"], ["char: Char (variable)"]),
]


def synth_db() -> OpcodeDB:
    return OpcodeDB.from_rows([{"op": r[0], "name": r[1], "ext": r[2], "flags": r[3], "input": r[4], "output": r[5]}
                               for r in ROWS], "synthetic")


#: A CLEO script touching every parameter form of the text syntax.
CLEO_TEXT = r"""{$CLEO .cs}
03A4: script_name 'SYNTH'
0006: set_lvar_int 0@ 0
:LOOP
0001: wait 0
0111: test_any 5 -129 70000 -2147483648
0111: test_any 5:i32 5:i16 300:i32 -1:i16
0111: test_any 1.5 -0.0 0.1 3.4028235e+38
0111: test_any 0x7FC00000:f 1.0e-08 $12 &13
0111: test_any 0@ 7@s 8@v s$4
0111: test_any v$5 s&6 v&7 $3(0@,10i)
0111: test_any 2@(1@,4f) s$20($5,3s) 4@s(0@,2s) v$30(1@,2v)
0111: test_any 5@v(1@,2v) $40(&41,8t9) 'ABC' 'A\x00B\x01'
0111: test_any v'LONGERTEXT12345' "var len \"q\" \\ \xFF" "" 'X'
03A4: script_name r'RAWNAME'
0111: test_any true false 0x123456 -0x123456
05B6: save_string_to_debug_file r'DEBUG\x00\x12garbage'
00D6: if 21
0256: is_player_playing $2
8039: not is_int_lvar_equal_to_number 0@ 3
004D: goto_if_false @SKIP
00A0: get_char_coordinates $3 1@ 2@ 3@
0213: create_pickup 1240 3 1.0 2.0 3.0 4@
:SKIP
0AB1: cleo_call @FUNC 2 0@ 1.5 5@
0AD1: print_formatted_now "%d %.1f" 100 0@ 1@
000A: add_val_to_int_lvar 0@ 1
00D6: if 0
0039: is_int_lvar_equal_to_number 0@ 10
004D: goto_if_false @LOOP
0A93: terminate_this_custom_script
hex
  90 90 C3 00*12
end
:FUNC
0AB2: cleo_return 1 0@
"""

#: A main.scm with two missions (one empty name) and every header field.
MAIN_TEXT = r"""{$MAIN}
DEFINE GLOBALS_SIZE 64
DEFINE GLOBAL_BYTES 8 01020304
DEFINE OBJECTS 3
DEFINE OBJECT (noname)
DEFINE OBJECT MYOBJ
DEFINE OBJECT 'odd\x00\x07'
DEFINE MISSIONS 3
DEFINE MISSION 0 AT @INTRO_0
DEFINE MISSION 1 AT @M2_0
DEFINE MISSION 2 AT @INTRO_0
DEFINE EXCLUSIVE_MISSIONS 1
DEFINE MISSION_LOCALS 40
DEFINE EXTERNAL_SCRIPTS 1
DEFINE LARGEST_EXTERNAL_SIZE 100
DEFINE SCRIPT EXT1 OFFSET 5000 SIZE 100
DEFINE UNKNOWN_EMPTY_SEGMENT 0
DEFINE UNKNOWN_THREADS_MEMORY 7

03A4: script_name 'MAIN'
004F: start_new_script @MAIN_THREAD 1 2.5
0417: load_and_launch_mission_internal 0
:MAIN_LOOP
0001: wait 250
0002: goto @MAIN_LOOP
:MAIN_THREAD
0213: create_pickup #MYOBJ 3 1.0 2.0 3.0 $5
004E: terminate_this_script

{$MISSION}
:INTRO_0
03A4: script_name 'INTRO'
:INTRO_LOOP
0001: wait 0
0050: gosub @INTRO_SUB
0002: goto @MAIN_LOOP
:INTRO_SUB
0051: return

{$MISSION}
:M2_0
0000: nop
:M2_X
0002: goto @M2_X
"""

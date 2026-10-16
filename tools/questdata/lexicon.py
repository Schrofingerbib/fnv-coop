"""Script-function lexicon for the co-op risk classifier (see docs/04-quest-data.md section 6/9).

Entry: lowercase function name -> (category, level, level_if_player) where
  level           base level (0 safe, 1 sync-state, 2 per-player replicate, 3 needs design, 4 hard)
  level_if_player level used when the player is the call subject (`player.f`) or an argument.
Categories: Q-STATE, P-INV, P-STAT, P-REF, MOVE, CONV, CTRL, WORLD, ACTOR, SAVE, TIME.
Tiers T0..T4 equal the max level reached.
"""

PLAYER_NAMES = {'player', 'playerref'}

L = {}


def _add(cat, level, names, lvl_player=None):
    for n in names.split():
        L[n] = (cat, level, level if lvl_player is None else lvl_player)


_add('Q-STATE', 1, 'setstage getstage getstagedone setobjectivecompleted setobjectivedisplayed getobjectivecompleted '
                   'getobjectivedisplayed completequest failquest startquest stopquest getquestrunning getquestcompleted '
                   'setquestobject setglobalvalue getglobalvalue getquestvariable setquestdelay addtopic '
                   'resetquest getquestvar setquestvar')
_add('P-INV', 2, 'removeitem removeallitems equipitem unequipitem additemhealthpercent additemns dropme dropitem '
                 'removenote getitemcount getitemhealthpercent unequipall removeitemns removecaps',
     None)
L['removeallitems'] = ('P-INV', 2, 3)
_add('P-STAT', 2, 'modav setav forceav damageav restoreav modactorvalue setactorvalue forceactorvalue damageactorvalue '
                  'restoreactorvalue modpcmiscstat addperk removeperk modpcskill modpcattr '
                  'setpccrimeflags setpcexpelled '
                  'setplayerrace setpcisinfamous setpcfactionally setpcfactionenemy setpcfactionneutral payfine payfinethief '
                  'setcrimegold modcrimegold setpcisdetected')
# Owner decisions (2026-10-15): karma, faction reputation and player faction membership/rank are SHARED across the party (level 1);
# quest rewards (items/caps/xp/notes) go to every player (level 1, routed to all).
_add('S-STAT', 1, 'rewardkarma playerrewardkarma addreputation setreputation modreputation setpcfame setpcinfamy '
                'addtofaction removefromfaction setfactionrank modfactionrank')
_add('REWARD', 1, 'additem additemns additemhealthpercent addcaps addnote rewardxp')
# functions whose player subject is implicit (no `player.` prefix needed)
PLAYER_IMPLICIT = {'rewardxp', 'rewardkarma', 'playerrewardkarma', 'modpcmiscstat', 'addperk', 'removeperk',
                   'addreputation', 'setreputation', 'modreputation', 'setpcfame', 'setpcinfamy', 'addnote',
                   'removenote', 'modpcskill', 'modpcattr', 'setpcexpelled'}
# control-lock functions that, inside dialogue scripts, are handled by a generic dialogue-lock rule (level 2)
DIALOG_LOCK = {'disableplayercontrols'}
_add('CTRL-RELEASE', 2, 'enableplayercontrols enablecontrol')
_add('COMPANION', 3, 'setplayerteammate')
_add('TRAVEL', 1, 'enablefastravel enablefasttravel disablefasttravel setplayercantravel')

_add('P-REF', 3, 'getdistance getpcmiscstat getpcfame getpcinfamy getpcexpelled getpcisclass getpcissex getpcinfaction '
                 'getpcisrace getplayerteammate getplayerteammatecount getplayerhaslastriddenhorse '
                 'getplayercontrolsdisabled getdisposition getinsamecell getincell getinworldspace getlineofsight '
                 'getisreference getisid getactionref getplayerinseworld getpcsleepdeprivation getcurrenttarget '
                 'getdetected getdetectionlevel getlocked getinfaction getfactionrank getfactionreaction '
                 'getrelationshiprank getisplayablerace getpos getangle getpcinventory')
_add('MOVE', 1, 'moveto movetomarker setpos setangle centeroncell centeronworld centeroncellandupdate fasttravel '
                'teleport setposition', 1)
L['moveto'] = ('MOVE', 1, 4)
L['movetomarker'] = ('MOVE', 1, 4)
L['setpos'] = ('MOVE', 1, 4)
L['centeroncell'] = ('MOVE', 4, 4)
L['centeronworld'] = ('MOVE', 4, 4)
L['centeroncellandupdate'] = ('MOVE', 4, 4)
L['fasttravel'] = ('MOVE', 4, 4)
L['teleport'] = ('MOVE', 4, 4)
_add('CONV', 2, 'startconversation stopconversation showbartermenu say sayto showmessage messageex showmenu '
                'showdialogmenu showlockpickmenu showhackingmenu showcharactermenu showtraitmenu showperkmenu '
                'showrepairmenu showcompanionwheel showreputationmenu setnpcref')
_add('CTRL', 4, 'disableplayercontrols disablecontrol enablecontrol '
                'setcameraon forceplayercamera showplayercam setplayeraidriven setignorefriendlyhits '
                'startcinematic playbink '
                'setinchargen forcethirdperson forcefirstperson showracemenu setcamerapos setcamerapov '
                'setplayercamera disableallactors fadetoblack fadefromblack settimedialogue setcontrolsdisabled '
                'setplayerinchargen startmovie')
_add('WORLD', 1, 'forceweather setweather releaseweatheroverride setgamehour settimescale setglobaltimemultiplier '
                 'playmusic setimagespace imagespacemodifier setcellfullname setnoradio playsound placeatme '
                 'placeatmeobject markfordelete setownership setdestroyed lock unlock setopenstate activate '
                 'removeme createfullactorcopy setactorfullname setquestobject setlocked setcellpublicflag '
                 'playsound3d stopsound setdestructible')
_add('ACTOR', 1, 'enable disable kill killactor resurrect startcombat stopcombat stopcombatalarmonactor setessential '
                 'setalert setenemy setally setfactionreaction modfactionreaction setcombatstyle addscriptpackage '
                 'removescriptpackage evaluatepackage setrestrained setunconscious setghost setactoralpha '
                 'setdoordefaultopen setscale pushactoraway playgroup playidle forceactorvalue setactorstate '
                 'setallreachable setally setignorefriendlyhits setoutofrange follow stopwaiting addscriptpackage')
_add('SAVE', 2, 'save quicksave autosave setplayerskipssave playersavegame setchallengeprogress modchallengeprogress '
                'setdeadcountmult', 2)
L['quit'] = ('SAVE', 4, 4)
L['quitgame'] = ('SAVE', 4, 4)
L['loadgame'] = ('SAVE', 4, 4)
L['qqq'] = ('SAVE', 4, 4)
_add('TIME', 0, 'getsecondspassed getcurrenttime gettimedead getgamehour getdayssince getdayspassed')

# category sets used for roll-up flags
PLAYER_MUTATING = {'P-INV', 'P-STAT'}
CUTSCENE = {'CTRL'}

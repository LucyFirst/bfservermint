Brave Frontier private server
=============================

FIRST TIME
1. Install Python 3 from python.org (tick "Add python.exe to PATH").
2. Double-click IMPORT-FROM-EMULATOR.bat
   - copies the game files you copied out of LDPlayer (..\sg.gumi.bravefrontier\files)
     into cdn\ in the right folders. Fast, no internet needed.
   Then double-click EXTRACT-CONTENT.bat
   - unpacks art/sound/data from every .flow recording in the folder above into cdn\
     (run it again whenever you add a new recording).
   OR (instead of / as well as step 2) double-click DOWNLOAD-ALL-CONTENT.bat
   - downloads the game's art, animations, effects and sound straight from
     bfrevive's content server into cdn\ (no recording needed). Can take a
     while and several GB; stop any time with Ctrl+C and run again to resume.
3. Double-click START-SERVER.bat
   - Windows Firewall will ask: allow on Private networks.
4. In LDPlayer:
   - switch the WireGuard tunnel OFF (mitmproxy is not needed any more)
   - uninstall the old Brave Frontier, install bf-private.apk
   - start the game and create an account.

SETTINGS (config.json)
  host   your PC's local IP address (currently 192.168.1.71). If it changes,
         the APK must be rebuilt for the new address. Reserve the address in your
         router to avoid this.
  port   443 (keep it unless something else uses it)
  fetch_missing_content
         true = when the game asks for a file you don't have yet, the server
         fetches it from bfrevive's content server once and keeps it.
         Set to false to run fully offline.

FILES
  saves\         your accounts and progress (back this folder up)
  server.log     everything the server did; lines marked WARNING are things the
                 game asked for that the server can't answer yet
  data\          responses recorded from the capture
  certs\         the server's certificate authority (the APK trusts ca.crt)

WHAT WORKS
  Login, account creation, name, tutorial, home screen, team/deck editing,
  equipment, quests (missions 1, 2, 10, 11, 12, 20, 21, 110340), summons, fusion,
  selling units, item crafting, town upgrades, arena, friend requests, presents,
  achievements, daily tasks and claims, event tokens.
  Account, units, team and items are saved per player.

LIMITS OF THIS VERSION
  Quests, summons, fusion etc. are REPLAYED from the recorded session: the server
  picks the recorded result that best matches what you did. So summon results and
  quest rewards repeat what was recorded, and quests that weren't played in the
  recording show "Not available on this private server yet". The next step is
  computing these results properly from the game data.

ADMIN (gems, zel, energy...)
  Double-click ADMIN.bat (next to START-HERE.bat). Each account keeps its own
  balances; summons, quests, fusion etc. spend and earn from them.
    gems 50          give 50 gems (gems -10 takes some away)
    set gems 100     set an exact balance
    zel / karma / friendpoints / summontickets / rainbowcoins  work the same
    level 500, energy 999, unitbox 4000 ...  fix a value until 'reset level'
    show             current balances      players / use <name>  pick account
  Changes reach the game on its next screen change - no restart needed.

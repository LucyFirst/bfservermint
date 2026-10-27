"""One-off: first clears used to give 1 gem, now 5. Puts a gift in the gift box of every player who
cleared quests before the change: 4 gems for each quest they have cleared. Run once on the Droplet,
right after bf-update:   python3 /opt/bfserver/tools/firstclear_topup.py
Shows the amounts and asks before adding the gift. Running it again does nothing (the gift exists)."""
import os, sys, time, logging
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(HERE); sys.path.insert(0, HERE)
logging.disable(logging.CRITICAL)
import server as S

GIFT_ID = 'g_firstclear_topup'
A = S.admin_page()
gifts = A.gifts()
if any(g.get('id') == GIFT_ID for g in gifts):
    print('The first clear top-up gift already exists - nothing to do.')
    sys.exit(0)
owed = {}
for pl in sorted(S.all_players(), key=lambda p: str(p.get('handle'))):
    n = sum(1 for r in S._rows(pl['state'].get('UT1SVg59')) if isinstance(r, dict) and r.get('j28VNcUW'))
    if n:
        owed[pl['user_id']] = 4 * n
        print(f"{pl.get('handle'):<16} {pl['user_id']}  {n:>4} quests cleared  -> {4 * n} gems")
print(f'{len(owed)} players, {sum(owed.values())} gems in all')
if input('Add this gift? (yes/no) ').strip().lower() != 'yes':
    print('Nothing added.')
    sys.exit(0)
gifts.append({'id': GIFT_ID, 'type': 8, 'target': '', 'count': 0, 'per_player': owed,
              'label': f'gems, 4 per quest already cleared ({len(owed)} players)',
              'message': 'First clear bonus top-up (now 5 gems per clear)', 'created': int(time.time())})
A.save_gifts(gifts)
print('Added. Each player finds it in their gift box the next time they open the game.')

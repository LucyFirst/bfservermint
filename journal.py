"""
Summoner Journal: a list of tasks with rewards, and journal points that reach milestones.

The game (Rewards menu > Summoner Journal, from the game's own code - there are no recordings of it):
  SummonerJournalInfoRequest          the journal screen opens
  SummonerJournalTaskRewardsRequest   da38tRai[0].23DaiBpe = task id(s) to claim ("1" or "1,2,3" claim all)
  SummonerJournalMilestoneRewardsRequest  never sent by this game version
Reply groups (each row is added to the game's list when its LAST key is read, so key order matters):
  T38aBiw3 tasks       23DaiBpe id, s35idar9 name, H2Dnbr39 note, v39D198k locked level, xNd38t2u unlock
                       type (1 level, 2 task), Sd38Dbt3 unlock value, pG2n1A28 target, w3Di51bp points,
                       da36ky2E Go button screen (1 Units 2 Town 3 Quests 4 Vortex 5 Arena 6 Randall
                       7 Raid 8 Friends 9 Gift box)
  b2DjiaXp rewards     23DaiBpe task, S1B82FHK present id (item / unit), 37moriMq, ZC0msu2L, 30Kw4WBa
                       present type, wJsB35iH count
  ad52Diwq milestones  Uiwd28Vq id, S1B82FHK present id, TdDHf59J, 37moriMq, ZC0msu2L, 9hH0neGa points,
                       30Kw4WBa present type, wJsB35iH count
  M3dw18eB user        h7eY3sAK user, 9hH0neGa points, da365dB8 1 = journal button shown (Rewards menu)
  da38tRai user tasks  h7eY3sAK, 23DaiBpe task, pG2n1A28 progress, 2g6adYig 0 = can be claimed
                       (anything else: not done), b638DwP8 1 = open (0 = locked)
  r3D28bqW milestones just reached  h7eY3sAK, Uiwd28Vq id, da365dB8 - the game shows a "reward
                       received" popup for each, then forgets them
Claimed tasks are left out of the list. Rewards (tasks and milestones) go to the gift box.
Progress is read from what the server already keeps (level, cleared quests, records, counters), so
nothing new has to be tracked. The tasks and milestones are in data/summoner_journal.json.
"""
import json
import os

G_TASK, G_REWARD, G_MILE = 'T38aBiw3', 'b2DjiaXp', 'ad52Diwq'
G_USER, G_UTASK, G_UMILE = 'M3dw18eB', 'da38tRai', 'r3D28bqW'


def _i(x, d=0):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return d


class Journal:
    def __init__(self, srv):
        self.S = srv
        self.file = os.path.join(srv.HERE, 'data', 'summoner_journal.json')
        self.mtime, self.tasks, self.miles = None, [], []
        self._load()

    def _load(self):
        try:
            mt = os.path.getmtime(self.file)
        except OSError:
            return
        if mt == self.mtime:
            return
        d = json.load(open(self.file, encoding='utf-8'))
        self.tasks = [t for t in d.get('tasks') or [] if t.get('id')]
        self.miles = sorted((m for m in d.get('milestones') or [] if m.get('id')), key=lambda m: _i(m['points']))
        self.mtime = mt

    def enabled(self):
        return bool(self.S.admin_settings().get('features', {}).get('summoner_journal', 1))

    # ------------------------------------------------------------------ player state
    def mine(self, pl):
        j = pl.setdefault('journal', {})
        j.setdefault('claimed', [])
        j.setdefault('miles', [])
        return j

    def points(self, pl):
        by = {t['id']: t for t in self.tasks}
        return sum(_i(by[t].get('points')) for t in self.mine(pl)['claimed'] if t in by)

    def progress(self, pl, goal):
        S, st = self.S, pl.get('state') or {}
        goal = str(goal or '')
        if goal == 'level':
            return _i((S.team_row(st) or {}).get('D9wXQI2V'))
        if goal == 'quests':
            return len(set(S.cleared_ids(pl)))
        if goal == 'summons':
            return _i(pl.get('summons_total'))
        if goal == 'units':
            um = S.unit_mst()
            return len({r.get('pn16CNah') for r in st.get(S.G_DICT) or [] if isinstance(r, dict)
                        and str((um.get(str(r.get('pn16CNah'))) or {}).get('xP9oi5Z2', '0')) == '0'})
        if goal.startswith('rec:'):
            return _i(S.records_engine().row(pl).get(goal[4:]))
        if goal.startswith('count:'):
            return _i((pl.get('counts') or {}).get(goal[6:]))
        if goal.startswith('quest:'):
            return 1 if S.cleared_before(pl, goal[6:]) else 0
        return 0

    def unlocked(self, pl, t):
        u = t.get('unlock') or {}
        if 'level' in u and self.progress(pl, 'level') < _i(u['level']):
            return False
        if 'task' in u and str(u['task']) not in self.mine(pl)['claimed']:
            return False
        return True

    def claimable(self, pl, t):
        return t['id'] not in self.mine(pl)['claimed'] and self.unlocked(pl, t) and \
            self.progress(pl, t.get('goal')) >= _i(t.get('target'), 1)

    # ------------------------------------------------------------------ reply
    def reply(self, pl, new_miles=()):
        uid = pl['user_id']
        claimed = set(self.mine(pl)['claimed'])
        tasks, rewards, utasks = [], [], []
        for t in self.tasks:
            u = t.get('unlock') or {}
            ptype, pid, cnt = (list(t.get('reward') or [8, '', 0]) + ['', 0])[:3]
            tasks.append({'23DaiBpe': t['id'], 's35idar9': t.get('name', ''), 'H2Dnbr39': t.get('note', ''),
                          'v39D198k': str(_i(u.get('level'))), 'xNd38t2u': '1' if 'level' in u else '2' if 'task' in u else '0',
                          'Sd38Dbt3': str(u.get('level') or u.get('task') or 0),
                          'pG2n1A28': str(_i(t.get('target'), 1)), 'w3Di51bp': str(_i(t.get('points'))),
                          'da36ky2E': str(_i(t.get('screen')))})
            rewards.append({'23DaiBpe': t['id'], 'S1B82FHK': str(pid or ''), '37moriMq': '',
                            'ZC0msu2L': t.get('name', ''), '30Kw4WBa': str(ptype), 'wJsB35iH': str(cnt)})
            if t['id'] in claimed:
                continue
            target = _i(t.get('target'), 1)
            done = self.progress(pl, t.get('goal'))
            opened = self.unlocked(pl, t)
            utasks.append({'h7eY3sAK': uid, '23DaiBpe': t['id'], 'pG2n1A28': str(min(done, target)),
                           '2g6adYig': '0' if opened and done >= target else '1', 'b638DwP8': '1' if opened else '0'})
        if not utasks:
            # The game only clears its task list when a row arrives - an empty list leaves the last
            # claimed task on screen with its Receive button. With everything claimed, show the
            # finished tasks (full progress, Go button) instead.
            utasks = [{'h7eY3sAK': uid, '23DaiBpe': t['id'], 'pG2n1A28': str(_i(t.get('target'), 1)),
                       '2g6adYig': '1', 'b638DwP8': '1'} for t in self.tasks]
        miles = []
        for m in self.miles:
            ptype, pid, cnt = (list(m.get('reward') or [8, '', 0]) + ['', 0])[:3]
            miles.append({'Uiwd28Vq': m['id'], 'S1B82FHK': str(pid or ''), 'TdDHf59J': str(pid or ''), '37moriMq': '',
                          'ZC0msu2L': m.get('message', ''), '9hH0neGa': str(_i(m['points'])),
                          '30Kw4WBa': str(ptype), 'wJsB35iH': str(cnt)})
        resp = {G_TASK: tasks, G_REWARD: rewards, G_MILE: miles, G_USER: self.user_row(pl), G_UTASK: utasks}
        if new_miles:
            resp[G_UMILE] = [{'h7eY3sAK': uid, 'Uiwd28Vq': m, 'da365dB8': '1'} for m in new_miles]
        return resp

    def user_row(self, pl, row=None):
        row = dict(row or {})
        row.update({'h7eY3sAK': pl['user_id'], '9hH0neGa': self.points(pl), 'da365dB8': 1 if self.enabled() else 0})
        row.setdefault('3a8b9D8i', 0)
        return row

    # ------------------------------------------------------------------ requests
    def _milestones(self, pl):
        """Milestones the points have reached that weren't given yet: to the gift box."""
        j, pts, new = self.mine(pl), self.points(pl), []
        for m in self.miles:
            if m['id'] not in j['miles'] and pts >= _i(m['points']):
                j['miles'].append(m['id'])
                ptype, pid, cnt = (list(m.get('reward') or [8, '', 0]) + ['', 0])[:3]
                if _i(cnt):
                    self.S.add_present(pl, ptype, pid, cnt, m.get('message') or 'Summoner Journal milestone')
                new.append(m['id'])
        return new

    def info(self, pl):
        self._load()
        return self.reply(pl, self._milestones(pl))

    def claim(self, pl, req):
        self._load()
        sel = (req.get(G_UTASK) or [{}])[0]
        want = [x.strip() for x in str(sel.get('23DaiBpe') or '').split(',') if x.strip()]
        by = {t['id']: t for t in self.tasks}
        got = []
        for tid in want:
            t = by.get(tid)
            if t is None or not self.claimable(pl, t):
                continue
            self.mine(pl)['claimed'].append(tid)
            ptype, pid, cnt = (list(t.get('reward') or [8, '', 0]) + ['', 0])[:3]
            if _i(cnt):
                self.S.add_present(pl, ptype, pid, cnt, f"Summoner Journal: {t.get('name', '')}")
            got.append(t.get('name', tid))
        if not got:
            # already claimed (the game's list was out of date): send the journal as it is, no error
            self.S.log.info('Summoner Journal: nothing to claim in %s', want)
            return self.reply(pl, self._milestones(pl)), None
        self.S.log.info('Summoner Journal: claimed %s (%s points)', ', '.join(got), self.points(pl))
        resp = self.reply(pl, self._milestones(pl))
        resp[self.S.G_PRESENTS] = self.S.present_rows(pl)
        return resp, None

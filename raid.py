"""
Raid Battle (solo and multiplayer) - built from the bfrevive Raid recordings (RAID-PLAN.txt).

Everything goes through action.php like any other request. Players in the same room poll the
room (RaidRoomInfo) and, once the host starts, the shared quest (RaidMissionInfo) every few
seconds, so "multiplayer" is just several players reading and changing the same room / quest
kept here in memory: the 30-minute timer, the bosses' HP, everyone's place on the map.

The game's own Raid tables are in data/raid_data.json and one recorded row per reply group is in
data/raid_templates.json (tools/build_raid_data.py makes both). Recordings are only references:
every value sent is this server's own.

Request -> what happens here
  Agreement / UserInfo / WorloInfo   raid profile, quests open for your Raid Class, cleared quests
  RoonItemEdit                       the two item bags taken into Raid (main / sub)
  UpDataRoomInfo                     make a room (or change its settings)
  QuickStart                         make a room for one quest and go
  RoomList / Enter / Exit / Kick / Dissolve / UpDataUserReady / RoomFriendGet / FrlendSelect
  RoomInfo                           the room (polled)
  MissionStart                       host: start the quest for the room; everyone: join it
  MissionInfo / MissionInfoLight     the quest (polled; carries your place on the map)
  MissionHarvest                     search a point: one item from the point's drop list, 3 times
  CampRestStart / Get / End          rest at a camp: the squad heals 10% of max HP every 4 s
  LimitedUseItem / ItemEdit          use an item on the map / move items between item set and bag
  ItemMix / ItemDelete               craft from the bag (town recipes, no karma) / throw away
  ShopUse                            gem shop: 100 refill Raid entries, 101 refill hearts
  MissionBattleStart / BattleRestart a fight: small enemies, or the boss with its parts' HP now
  UpdateProgress                     damage done to the boss during the fight (shared)
  MissionBattleEnd                   end of a fight (last damage, units' HP; a wiped squad costs a
                                     heart and comes back at full HP; items the fight dropped)
  MissionEnd / MissionExit           rewards (zel, karma, medals, what you picked up, Raid Class up)
  SendChat / GetChatLog              room stamps
"""
import json
import os
import random
import threading
import time

MAX_MEMBERS = 4
TICKETS, TICKET_SECONDS = 3, 3600        # Raid entries: 3, one comes back every hour (as bfrevive)
JOIN_SECONDS = 300                       # after the host starts, others can still join for 5 min
ENDED_WAIT = 600                         # a finished quest waits this long for everyone's results
ROOM_IDLE = 15 * 60                      # rooms nobody has polled for this long are closed
BOSS_HP, BOSS_ATK, BOSS_DEF = 40000, 450, 80     # bfrevive's Raid bosses (Raid Class 1 quests)
CLASS_MULT = {1: 1.0, 2: 2.0, 3: 3.0, 4: 4.0, 5: 5.0, 6: 6.0, 7: 7.0}   # harder Raid Classes
MAX_CLASS = 7
HARVEST_EMPTY = 'There is nothing more to find here.'
LIVES = 3                                # hearts on the map; a wiped squad costs one (game: raid_max_p)
CAMP_TICK, CAMP_HEAL = 4, 10             # resting heals 10% of max HP every 4 s (the game's camp)
SHOP_ENTRIES, SHOP_LIVES = 100, 101      # Raid gem shop: refill entries / refill hearts
SHOP_GEMS = 1                            # gems per refill (as continuing a quest costs here)
BAG_STACKS = 50                          # picked-up items the Raid bag holds (game: raid_bag_cnt)

# the map enemies bfrevive uses in every Raid quest (Moize Plains' fire set), and how often
SMALL_ENEMIES = {'12300': 3, '11600': 3, '14550': 3, '11550': 3, '60550': 1, '10650': 1}
LAYOUT = {1: ['180:302'], 2: ['180:302', '120:248'], 3: ['180:302', '120:248', '96:352'],
          4: ['160:240', '215:300', '160:360', '105:300']}
BOSS_POS_MAIN, BOSS_POS_PART = '180:330', '120:300'


def _i(x, d=0):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return d


def _row(g, req):
    v = req.get(g)
    if isinstance(v, list):
        return v[0] if v and isinstance(v[0], dict) else {}
    return v if isinstance(v, dict) else {}


def _rows(g, req):
    v = req.get(g)
    if isinstance(v, dict):
        return [v]
    return [r for r in (v or []) if isinstance(r, dict)]


class RaidError(Exception):
    pass


class Raid:
    def __init__(self, srv):
        self.S = srv
        here = srv.HERE
        self.D = json.load(open(os.path.join(here, 'data', 'raid_data.json'), encoding='utf-8'))
        self.T = json.load(open(os.path.join(here, 'data', 'raid_templates.json'), encoding='utf-8'))
        self.R = self.T['rows']
        self.mu = threading.RLock()
        self.rooms = {}            # room id -> room
        self.quests = {}           # quest (mission instance) id -> quest
        self.where = {}            # user id -> room id
        self.prof = {}             # user id -> what other players see of them (kept fresh)
        base = int(time.time()) % 100000
        self.next_room, self.next_quest, self.next_battle = 10000 + base, 50000 + base, 1005000000 + base * 100

    # ------------------------------------------------------------------ basics
    def enabled(self):
        try:
            return bool(self.S.admin_settings().get('features', {}).get('raid'))
        except Exception:
            return False

    def tmpl(self, g):
        return dict(self.R.get(g) or {})

    def error(self, msg):
        """bfrevive answers Raid problems with a plain message box (no reply data)."""
        return {'_plain': {'b5PH6mZa': {'3e9aGpus': 1, 'iPD12YCr': 2, 'ZC0msu2L': msg, 'zcJeTx18': ''}}}

    def supported(self, mid):
        m = self.D['missions'].get(str(mid))
        if not m or not m.get('bosses'):
            return False
        return all((self.D['bosses'].get(b['boss']) or {}).get('parts') for b in m['bosses'])

    def offered(self, pl):
        cls = self.state(pl)['cls']
        return [mid for mid in self.D.get('offered', []) if self.supported(mid) and self.D['missions'][mid]['cls'] <= cls]

    # ------------------------------------------------------------------ player raid state
    def state(self, pl):
        st = pl.setdefault('raid', {})
        st.setdefault('agreed', 0)
        st.setdefault('cls', 1)
        st.setdefault('cleared', [])
        st.setdefault('tickets', TICKETS)
        st.setdefault('tick_t', 0)
        st.setdefault('bag', [])
        st.setdefault('sub', [])
        st.setdefault('squad_no', '0')
        st.setdefault('stats', {})
        # entries come back over time
        if st['tickets'] < TICKETS and st['tick_t']:
            n = int((time.time() - st['tick_t']) // TICKET_SECONDS)
            if n > 0:
                st['tickets'] = min(TICKETS, st['tickets'] + n)
                st['tick_t'] += n * TICKET_SECONDS
        if st['tickets'] >= TICKETS:
            st['tick_t'] = 0
        return st

    def ticket_timer(self, st):
        if not st['tick_t'] or st['tickets'] >= TICKETS:
            return 0
        return max(0, int(TICKET_SECONDS - (time.time() - st['tick_t'])))

    def use_ticket(self, st):
        if st['tickets'] >= TICKETS:
            st['tick_t'] = time.time()
        st['tickets'] -= 1

    def user_row(self, pl):
        st = self.state(pl)
        rid = self.where.get(pl['user_id'])
        row = self.tmpl('1ry6BKoe')
        row.update({'7x3pPB2C': str(st['cls']), 'dziN1Ha9': '1' if st['agreed'] else '0',
                    '43hMuY2I': '1' if rid else '', 'Z0Y4RoD7': _i(st['squad_no']), '8VYd6xSX': str(rid or ''),
                    'C45mRxnZ': str(st['tickets']), 'S92Hcor3': str(TICKETS), '6d2XBDUu': self.ticket_timer(st),
                    'iFA0fd6Y': '1'})
        return row

    def mission_rows(self, pl):
        return [{'j28VNcUW': mid, 'qY49LBjw': str(self.D['missions'][mid]['time'])} for mid in self.offered(pl)]

    def cleared_rows(self, pl):
        return [{'h7eY3sAK': pl['user_id'], 'j28VNcUW': m} for m in self.state(pl)['cleared']]

    def lobby(self, pl):
        return {'1ry6BKoe': [self.user_row(pl)], '2WqoMm4d': self.mission_rows(pl), '8LF2ohCY': self.cleared_rows(pl)}

    # ------------------------------------------------------------------ squads (squad type 3)
    def squads(self, pl):
        """The raid squads: the player's own (kept from their squad edits), else copies of their quest
        squads. Squads with a unit the player no longer has are copied again."""
        S = self.S
        st = self.state(pl)
        have = set(pl['units'])
        quest = [r for r in S._rows(pl['state'].get(S.G_PARTY)) if isinstance(r, dict) and str(r.get(S.F_PARTY_KIND)) == '1']
        mine = [r for r in st.get('squads') or [] if isinstance(r, dict)]
        out = []
        for no in sorted({str(r.get('zsiAn9P1')) for r in quest} | {str(r.get('zsiAn9P1')) for r in mine}, key=_i):
            rows = [r for r in mine if str(r.get('zsiAn9P1')) == no]
            ok = rows and all(r.get('edy7fq3L') in have for r in rows) and any(str(r.get('gr48vsdJ')) == '0' for r in rows)
            if not ok:
                rows = [dict(r) for r in quest if str(r.get('zsiAn9P1')) == no and r.get('edy7fq3L') in have]
            for r in rows:
                r = dict(r)
                r[S.F_PARTY_KIND] = S.RAID_SQUAD
                out.append(r)
        return out

    def save_squads(self, pl, req):
        """DeckEdit with raid squad rows: keep them. Returns True when only raid squads were edited
        (then the squad choice it carries is the raid squad, not the quest squad)."""
        S = self.S
        rows = [r for r in S._rows(req.get(S.G_PARTY)) if isinstance(r, dict)]
        raid = [dict(r) for r in rows if str(r.get(S.F_PARTY_KIND)) == S.RAID_SQUAD]
        if not raid:
            return False
        st = self.state(pl)
        st['squads'] = raid
        only = len(raid) == len(rows)
        if only and _row('Ti62XfZK', req).get('Z0Y4RoD7') is not None:
            st['squad_no'] = str(_row('Ti62XfZK', req).get('Z0Y4RoD7'))
        S.log.info('raid squads saved for %s (raid squad %s)', pl.get('handle'), st['squad_no'])
        return only

    def squad(self, pl):
        st = self.state(pl)
        rows = [r for r in self.squads(pl) if str(r.get('zsiAn9P1')) == str(st['squad_no'])]
        if not rows:
            rows = [r for r in self.squads(pl) if str(r.get('zsiAn9P1')) == '0']
        return sorted(rows, key=lambda r: _i(r.get('XuJL4pc5')))

    def add_party(self, pl, resp):
        """Replies that carry the squad list get the raid squads when Raid is open."""
        S = self.S
        if not self.enabled() or not isinstance(resp, dict) or not isinstance(resp.get(S.G_PARTY), list):
            return
        rest = [r for r in resp[S.G_PARTY] if not (isinstance(r, dict) and str(r.get(S.F_PARTY_KIND)) == S.RAID_SQUAD)]
        resp[S.G_PARTY] = rest + self.squads(pl)

    # ------------------------------------------------------------------ what others see of a player
    def profile(self, pl):
        S = self.S
        t = S.team_row(pl['state']) or {}
        st = self.state(pl)
        sq = self.squad(pl)
        lead = next((r for r in sq if str(r.get('gr48vsdJ')) == '0'), sq[0] if sq else {})
        lu = pl['units'].get(lead.get('edy7fq3L')) or next(iter(pl['units'].values()), {})
        units = []
        for r in sq:
            u = pl['units'].get(r.get('edy7fq3L'))
            if not u:
                continue
            row = self.tmpl('9PS8BLiC')
            row.update({'h7eY3sAK': pl['user_id'], 'edy7fq3L': u.get('edy7fq3L'), 'pn16CNah': u.get('pn16CNah'),
                        '2pAyFjmZ': '0', 'zsiAn9P1': str(r.get('zsiAn9P1')), 'gr48vsdJ': str(r.get('gr48vsdJ')),
                        'XuJL4pc5': str(r.get('XuJL4pc5')), '4A6LzBxr': str(u.get('D9wXQI2V', '1')),
                        'e7DK0FQT': str(u.get('e7DK0FQT', '0')), 'cuIWp89g': str(u.get('cuIWp89g', '0')),
                        'TokWs1B3': str(u.get('TokWs1B3', '0')), 'Ge8Yo32T': str(u.get('Ge8Yo32T') or '0'),
                        'mZA7fH2v': str(u.get('mZA7fH2v') or '0'), 'nBTx56W9': str(u.get('nBTx56W9', '1')),
                        '98WfKiyA': ''})
            units.append(row)
        p = {'id': pl['user_id'], 'name': S.shown_name(pl), 'level': str(t.get('D9wXQI2V', '1')),
             'leader': str(lu.get('pn16CNah', '0')), 'leader_lv': str(lu.get('D9wXQI2V', '1')),
             'cls': str(st['cls']), 'tickets': str(st['tickets']), 'units': units, 'seen': time.time()}
        self.prof[pl['user_id']] = p
        return p

    # ------------------------------------------------------------------ rooms
    def room_of(self, pl, req=None):
        rid = str(_row('8iBuZa3n', req or {}).get('8VYd6xSX') or self.where.get(pl['user_id']) or '')
        room = self.rooms.get(rid)
        return room

    def new_room(self, pl, mission, settings):
        self.leave(pl)
        self.next_room += 1
        rid = str(self.next_room)
        room = {'id': rid, 'host': pl['user_id'], 'mission': str(mission), 'settings': dict(settings),
                'members': [pl['user_id']], 'info': {pl['user_id']: self.member_info()}, 'state': 0, 'quest': None,
                'chat': [], 'chat_seq': 0, 'created': time.time(), 'seen': time.time()}
        self.rooms[rid] = room
        self.where[pl['user_id']] = rid
        self.S.log.info('raid: %s made room %s for quest %s (%s)', pl.get('handle'), rid, mission,
                        self.D['missions'].get(str(mission), {}).get('name'))
        return room

    @staticmethod
    def member_info():
        return {'ready': 0, 'helper': None, 'status': 0}

    def leave(self, pl):
        uid = pl['user_id']
        rid = self.where.pop(uid, None)
        room = self.rooms.get(rid)
        if not room:
            return
        q = self.quests.get(room.get('quest'))
        if q and uid in q['players']:
            q['players'][uid]['left'] = True
        if uid in room['members']:
            room['members'].remove(uid)
        room['info'].pop(uid, None)
        if not room['members']:
            self.close_room(room)
        elif room['host'] == uid:
            room['host'] = room['members'][0]
        self.S.log.info('raid: %s left room %s', pl.get('handle'), rid)

    def close_room(self, room):
        for uid in room['members']:
            if self.where.get(uid) == room['id']:
                self.where.pop(uid, None)
        self.rooms.pop(room['id'], None)
        if room.get('quest'):
            self.quests.pop(room['quest'], None)

    def tidy(self):
        now = time.time()
        for room in list(self.rooms.values()):
            if now - room['seen'] > ROOM_IDLE:
                self.S.log.info('raid: room %s closed (nobody there)', room['id'])
                self.close_room(room)
                continue
            q = self.quests.get(room.get('quest'))
            if q:
                self.tick(q)
                # a finished quest someone never came back from (closed the app): the room goes on
                if q['phase'] != 1 and now - q.get('ended', now) > ENDED_WAIT:
                    self.S.log.info('raid: quest %s closed (not everyone came back for the results)', q['id'])
                    self.quests.pop(q['id'], None)
                    room['quest'], room['state'] = None, 0
                    for i in room['info'].values():
                        i.update(status=0, ready=0)

    def room_row(self, room):
        host = self.prof.get(room['host']) or {}
        q = self.quests.get(room.get('quest'))
        row = self.tmpl('8iBuZa3n')
        row.update({k: str(v) for k, v in room['settings'].items() if k in row})
        left = max(0, int(JOIN_SECONDS - (time.time() - q['start']))) if q and room['state'] == 1 else 0
        row.update({'8VYd6xSX': room['id'], '43hMuY2I': '1', '8ZK3w0Np': room['host'],
                    'IAfv98Wd': host.get('name', ''), '6WKvM4o8': host.get('level', '1'),
                    '5bBwEo9f': host.get('leader', '0'), 'fZgEP96L': host.get('leader_lv', '1'),
                    'j28VNcUW': room['mission'], '3tWuc7de': str(room['state']), 'rPIya39u': str(len(room['members'])),
                    'WDUyI85C': str(room['settings'].get('WDUyI85C', row.get('WDUyI85C', ''))),
                    '4Qf8RePm': str(left), 'C1vG0iKh': '1' if room['state'] == 0 or left > 0 else '0',
                    'GWfpjED4': '0', 'XqMsnE89': '0', 'i9L42Gx0': '0', 'TdDHf59J': ''})
        return row

    def member_rows(self, room):
        out = []
        q = self.quests.get(room.get('quest'))
        for uid in room['members']:
            p = self.prof.get(uid) or {}
            info = room['info'].get(uid) or self.member_info()
            row = self.tmpl('uwH62EZy')
            row.update({'8VYd6xSX': room['id'], 'h7eY3sAK': uid, 'B5JQyV8j': p.get('name', ''),
                        'F6bPj50Q': p.get('leader', '0'), '7x3pPB2C': p.get('cls', '1'), 'D9wXQI2V': p.get('level', '1'),
                        'C45mRxnZ': p.get('tickets', '0'), 'uX87tAZB': str(info['ready']), 'j3g5P4cq': str(info['status']),
                        'FV6MU5nQ': str(q['id']) if q and info['status'] else '0', 'j28VNcUW': room['mission'],
                        'D2ZKtGR9': '0', 'BY8fZ7M1': '1', 'Tq86EG0N': '0', '2pAyFjmZ': '0', 'sD73jd20': '0'})
            out.append(row)
        return out

    def room_units(self, room):
        return [u for uid in room['members'] for u in (self.prof.get(uid) or {}).get('units', [])]

    def room_groups(self, pl, room, units=True):
        out = {'8iBuZa3n': [self.room_row(room)], 'uwH62EZy': self.member_rows(room)}
        if units:
            out['9PS8BLiC'] = self.room_units(room)
        return out

    # ------------------------------------------------------------------ chat
    def chat_since(self, room, req):
        cur = _i(_row('pw38qdui', req).get('NLKce95n'))
        return [c for c in room['chat'] if _i(c['NLKce95n']) > cur]

    def add_chat(self, pl, room, req):
        c = _row('JQ6hW0Ma', req)
        p = self.prof.get(pl['user_id']) or self.profile(pl)
        room['chat_seq'] += 1
        row = self.tmpl('WCA9VhL3')
        row.update({'NLKce95n': str(room['chat_seq']), 'ToSxkU34': time.strftime('%H:%M'), 'h7eY3sAK': pl['user_id'],
                    'B5JQyV8j': p['name'], 'pn16CNah': p['leader'], '8Fj06n3K': str(c.get('8Fj06n3K', '2')),
                    '3kQUaGx0': str(c.get('3kQUaGx0', '1')), '2pAyFjmZ': '1'})
        room['chat'] = (room['chat'] + [row])[-50:]

    # ------------------------------------------------------------------ the quest (mission instance)
    def start_quest(self, room):
        m = self.D['missions'][room['mission']]
        mult = CLASS_MULT.get(m['cls'], 1.0)
        self.next_quest += 1
        bosses = []
        for b in m['bosses']:
            parts = {}
            for p in self.D['bosses'][b['boss']]['parts']:
                parts[p['id']] = int(BOSS_HP * mult) if p['main'] else int(BOSS_HP * mult / 3)
            bosses.append({'id': b['id'], 'boss': b['boss'], 'point': b.get('point') or '1',
                           'shown': b.get('shown', '1'), 'parts': parts, 'max': dict(parts)})
        q = {'id': str(self.next_quest), 'room': room['id'], 'mission': room['mission'], 'start': time.time(),
             'limit': m['time'], 'phase': 1, 'left_at_end': None, 'bosses': bosses, 'players': {}, 'mult': mult}
        self.quests[q['id']] = q
        room['quest'], room['state'] = q['id'], 1
        for uid in room['members']:
            room['info'][uid].update(status=1)
        return q

    def time_left(self, q):
        if q['left_at_end'] is not None:
            return q['left_at_end']
        return max(0, int(q['limit'] - (time.time() - q['start'])))

    def tick(self, q):
        if q['phase'] == 1 and self.time_left(q) <= 0:
            q['phase'], q['left_at_end'], q['ended'] = 4, 0, time.time()
            self.set_status(q, 4)
            self.S.log.info('raid: quest %s (%s) ran out of time', q['id'], q['mission'])

    def set_status(self, q, st):
        room = self.rooms.get(q['room'])
        if not room:
            return
        for uid, ps in q['players'].items():
            if uid in room['info'] and not ps.get('done') and not ps.get('left'):
                room['info'][uid]['status'] = st

    def boss_alive(self, b):
        main = [pid for pid in b['parts'] if self._part(b['boss'], pid).get('main')]
        return any(b['parts'][pid] > 0 for pid in main)

    def _part(self, boss, pid):
        return next((p for p in self.D['bosses'][boss]['parts'] if p['id'] == pid), {})

    def main_hp(self, b):
        return sum(hp for pid, hp in b['parts'].items() if self._part(b['boss'], pid).get('main'))

    def check_won(self, q):
        if q['phase'] != 1:
            return
        m = self.D['missions'][q['mission']]
        dead = {b['id'] for b in q['bosses'] if not self.boss_alive(b)}
        need = [c.split(':')[1] for c in m.get('clear', []) if c.split(':')[0] == '1'] or [b['id'] for b in q['bosses']]
        if all(x in dead for x in need):
            q['phase'], q['left_at_end'], q['ended'] = 2, self.time_left(q), time.time()
            self.set_status(q, 2)
            self.S.log.info('raid: quest %s (%s) cleared', q['id'], m['name'])

    def player(self, q, pl):
        return q['players'].get(pl['user_id'])

    def join_quest(self, pl, q, req):
        st = self.state(pl)
        ps = q['players'].get(pl['user_id'])
        if ps and not ps.get('left') and not ps.get('done'):
            return ps                     # asked again (the game retries): same place, same squad
        units = []
        helper = (self.rooms[q['room']]['info'].get(pl['user_id']) or {}).get('helper')
        for r in _rows('wc2DiMY1', req):
            row = self.tmpl('wc2DiMY1')
            row.update({k: str(v) for k, v in r.items() if k in row})
            hp = str(r.get('e7DK0FQT', '0'))
            row.update({'h7eY3sAK': pl['user_id'], '3WMz78t6': hp, 'e7DK0FQT': hp, 'gU2xtQ0V': '0', 'nIGZ1X9C': '0',
                        '98WfKiyA': str(r.get('98WfKiyA') or (helper or {}).get('98WfKiyA', '') if str(r.get('edy7fq3L')) == '0' else '')})
            units.append(row)
        self.use_ticket(st)
        ps = {'pos': '1', 'visited': {}, 'camp': 0, 'camp_t': 0, 'lives': LIVES, 'battle': '0', 'boss_battle': 0, 'tries': {},
              'units': units, 'got': [], 'seqs': [], 'done': False, 'left': False, 'joined': time.time(),
              'unit_ids': [str(r.get('edy7fq3L')) for r in _rows('wc2DiMY1', req) if str(r.get('edy7fq3L')) != '0']}
        q['players'][pl['user_id']] = ps
        self.S.log.info('raid: %s joined quest %s (%s), %s entries left', pl.get('handle'), q['id'], q['mission'],
                        st['tickets'])
        return ps

    def visited_text(self, ps):
        return ','.join(f'{p}:{n}' for p, n in sorted(ps['visited'].items(), key=lambda x: _i(x[0])))

    def map_rows(self, q):
        out = []
        for uid, ps in q['players'].items():
            if ps.get('left') or ps.get('done'):
                continue
            p = self.prof.get(uid) or {}
            row = self.tmpl('81B4TvEK')
            tries = ps['tries']
            last = max(tries, key=lambda k: tries[k]) if tries else ''
            row.update({'h7eY3sAK': uid, 'B5JQyV8j': p.get('name', ''), 'k3I8AWLs': p.get('leader', '0'),
                        'pjX27bKB': ps['pos'], 'qbJcuC60': ps['pos'], 'fP1r39bd': str(ps.get('lives', LIVES)), 'MFz8YRS6': str(ps['camp']),
                        'zHs17TE2': self.visited_text(ps), 'EqgF1H8M': ps['battle'], '53NMFRL9': str(ps['boss_battle']),
                        '4rcdJRS6': '1' if tries else '0', '5soj5vGI': f'{last}:{tries[last]}' if tries else ''})
            out.append(row)
        return out

    def boss_rows(self, q):
        out = []
        for b in q['bosses']:
            alive = self.boss_alive(b)
            row = self.tmpl('Ts2zJ6gj')
            row.update({'SP29fLtH': b['id'], '4x7IS9Vg': str(self.main_hp(b)), '7Ex5mKRP': str(b['point']),
                        '5NAb7ds3': '0', '9P1MbHx5': '0', 'hsjMq7i6': '0', 'g57HTBUa': '0',
                        '7z6dqvuc': str(b['shown']) if alive else '0'})
            out.append(row)
        return out

    def got_rows(self, pl, ps):
        if not ps or not ps['got']:
            row = self.tmpl('UsDq1E6o')
            row.update({'h7eY3sAK': pl['user_id'], 'kixHbe54': '', 'wgV86x1q': '0', 'n6E8iMf3': '0', 'dJNpLc81': '0'})
            return [row]
        out = []
        for n, (item, cnt) in enumerate(ps['got'], 1):
            row = self.tmpl('UsDq1E6o')
            row.update({'n6E8iMf3': str(n), 'h7eY3sAK': pl['user_id'], 'kixHbe54': item, 'wgV86x1q': str(cnt),
                        'DbMVG16I': '0', 'dJNpLc81': '1', 'Slkc395l': ''})
            out.append(row)
        return out

    def take_got(self, ps, item, n):
        took = 0
        for e in ps['got']:
            if e[0] == str(item) and n > took:
                k = min(e[1], n - took)
                e[1] -= k
                took += k
        ps['got'] = [e for e in ps['got'] if e[1] > 0]
        return took

    def add_got(self, ps, item, n):
        for e in ps['got']:
            if e[0] == str(item):
                e[1] += n
                return
        ps['got'].append([str(item), n])

    def quest_groups(self, pl, room, q):
        self.tick(q)
        ps = self.player(q, pl)
        g = {'8iBuZa3n': [self.room_row(room)], 'uwH62EZy': self.member_rows(room)}
        mrow = self.tmpl('g97TWISb')
        mrow.update({'j28VNcUW': q['mission'], '3tWuc7de': str(q['phase']), '8n7oG4fS': str(self.time_left(q)),
                     'd8IPTUp6': '', 'de9f4qXb': ''})
        g['g97TWISb'] = [mrow]
        g['81B4TvEK'] = self.map_rows(q)
        g['Ts2zJ6gj'] = self.boss_rows(q)
        g['wc2DiMY1'] = [u for uid, p in q['players'].items() if not p.get('left') and not p.get('done') for u in p['units']]
        g['UsDq1E6o'] = self.got_rows(pl, ps)
        return g

    def update_units(self, ps, req):
        """The game reports the squad's HP / BB gauge after fights, items and while fighting."""
        if not ps:
            return
        by_id = {}
        for u in ps['units']:
            by_id[(u.get('edy7fq3L'), u.get('XuJL4pc5') if u.get('edy7fq3L') == '0' else '')] = u
        for r in _rows('wc2DiMY1', req):
            key = (str(r.get('edy7fq3L')), str(r.get('XuJL4pc5')) if str(r.get('edy7fq3L')) == '0' else '')
            u = by_id.get(key)
            if u is None:
                continue
            for k in ('e7DK0FQT', 'gU2xtQ0V', 'nIGZ1X9C'):
                if k in r:
                    u[k] = str(r[k])

    def apply_log(self, pl, q, ps, req):
        """QP4cwt7n: what happened in a fight, numbered so a resent entry counts once.
        NH65Wj0f 1 = damage 'boss:part:damage[,...]', 2 = item used '0:item:count'."""
        acks = []
        for e in _rows('QP4cwt7n', req):
            seq = str(e.get('o96QPNZn') or '')
            acks.append({'o96QPNZn': seq})
            if not seq or seq in ps['seqs']:
                continue
            ps['seqs'].append(seq)
            ps['seqs'] = ps['seqs'][-200:]
            kind = str(e.get('NH65Wj0f'))
            for part in str(e.get('xIB0C8PN') or '').split(','):
                bits = part.split(':')
                if len(bits) != 3:
                    continue
                if kind == '1':
                    b = next((x for x in q['bosses'] if x['id'] == bits[0]), None)
                    if b and bits[1] in b['parts'] and q['phase'] == 1:
                        b['parts'][bits[1]] = max(0, b['parts'][bits[1]] - _i(bits[2]))
                elif kind == '2':
                    self.take_bag_item(pl, bits[1], _i(bits[2]) or 1)
        self.check_won(q)
        return acks

    # ------------------------------------------------------------------ items
    def bag_rows(self, rows):
        return [{'kixHbe54': str(r.get('kixHbe54')), 'XuJL4pc5': str(r.get('XuJL4pc5', n)), 'wgV86x1q': str(r.get('wgV86x1q'))}
                for n, r in enumerate(rows) if r.get('kixHbe54') and _i(r.get('wgV86x1q')) > 0]

    def set_bags(self, pl, main, sub):
        """The two Raid item bags: never more of an item than the player has."""
        st = self.state(pl)
        left = {}
        out = []
        for rows in (main, sub):
            kept = []
            for r in self.bag_rows(rows):
                it = r['kixHbe54']
                if it not in left:
                    left[it] = self.S.item_count(pl, it, free_only=True)
                n = min(_i(r['wgV86x1q']), left[it])
                left[it] -= n
                if n > 0:
                    kept.append(dict(r, wgV86x1q=str(n)))
            out.append(kept)
        st['bag'], st['sub'] = out

    def take_bag_item(self, pl, item, n):
        st = self.state(pl)
        for bag in ('bag', 'sub'):
            for r in st[bag]:
                if r['kixHbe54'] == str(item) and n > 0:
                    k = min(n, _i(r['wgV86x1q']))
                    r['wgV86x1q'] = str(_i(r['wgV86x1q']) - k)
                    n -= k
                    self.S.warehouse_take(pl, str(item), k)
            st[bag] = [r for r in st[bag] if _i(r['wgV86x1q']) > 0]

    # ------------------------------------------------------------------ battles
    def enemy_row(self, mid, mult, rng):
        rows = self.T['monsters'].get(mid) or []
        row = dict(rng.choice(rows))
        j = rng.uniform(0.97, 1.03)
        for k in ('e7DK0FQT', '67CApcti', 'q08xLEsy'):
            row[k] = str(max(1, int(_i(row.get(k)) * mult * j)))
        return row

    def battle(self, pl, q, ps, req, point, boss_id):
        rng = random.Random()
        self.next_battle += 1
        bid = str(self.next_battle)
        mission = q['mission']
        g = {}
        if boss_id:
            b = next((x for x in q['bosses'] if x['id'] == boss_id), None)
            if b is None or not self.boss_alive(b):
                raise RaidError('This boss has already been defeated.')
            bdef = self.D['bosses'][b['boss']]
            tmpl = self.T['bosses_seen'].get('210001') or next(iter(self.T['bosses_seen'].values()))
            gid = str(20000000 + _i(boss_id) % 10000000)
            mons, lay, parts, idle = [], [], [], []
            n = 0
            for p in bdef['parts']:
                hp = b['parts'].get(p['id'], 0)
                if hp <= 0:
                    continue
                row = dict(tmpl)
                row.update({'o49dYfpH': p['id'], '3evIn0zZ': bdef['name'], 'e7DK0FQT': str(b['max'][p['id']]),
                            '67CApcti': str(int(BOSS_ATK * q['mult'])), 'q08xLEsy': str(int(BOSS_DEF * q['mult'])),
                            '2EF0d6ue': p['anime'], 'QqfI9mM4': p['cgg'], 'pn16CNah': '0'})
                mons.append(row)
                lay.append({'Qzhp8B40': gid, 'o49dYfpH': p['id'], 'hZtF1s8B': str(n),
                            '3g8PW6x0': BOSS_POS_MAIN if p['main'] else BOSS_POS_PART, 'hw3L0uVj': '0:0:0:0'})
                parts.append({'o49dYfpH': p['id'], '7Hf3DnVj': '1' if p['main'] else '0', 'jXEdk5J4': '0',
                              'e7DK0FQT': str(hp)})
                idle += [{'o49dYfpH': p['id'], 'fpc3rbs7': str(k), '0QxL2is7': p['idle']} for k in range(1, 5)]
                n += 1
            g['pj41dy9g'] = [{'ZSf8e1MG': '1', 'Qzhp8B40': gid, 'j28VNcUW': mission, 'VETu07N6': '1',
                              '5aetPz3C': '0', 'etM5TCb9': '1'}]
            g['75t0sx9z'], g['U0v5IeJo'], g['Fx60pCaU'], g['8hoyIF9Q'] = lay, mons, parts, idle
            ps['tries'][boss_id] = ps['tries'].get(boss_id, 0) + 1
            ps['boss_battle'] = 1
        else:
            pool = [m for m in SMALL_ENEMIES if self.T['monsters'].get(m)]
            weights = [SMALL_ENEMIES[m] for m in pool]
            cnt = rng.choice([3, 3, 4])
            picks = [rng.choices(pool, weights)[0] for _ in range(cnt)]
            mons = [self.enemy_row(m, q['mult'], rng) for m in picks]
            pos = LAYOUT[len(mons)]
            g['pj41dy9g'] = [{'ZSf8e1MG': '1', 'Qzhp8B40': '1', 'j28VNcUW': mission, 'VETu07N6': '1',
                              '5aetPz3C': '0', 'etM5TCb9': '0'}]
            g['75t0sx9z'] = [{'Qzhp8B40': '1', 'o49dYfpH': m['o49dYfpH'], 'hZtF1s8B': str(i), '3g8PW6x0': pos[i],
                              'hw3L0uVj': '0:0:0:0'} for i, m in enumerate(mons)]
            g['U0v5IeJo'] = mons
            ps['boss_battle'] = 0
        ai = {str(m.get('i74vGUFa', '1')) for m in g['U0v5IeJo']} | {'1'}
        g['89ausgc4'] = [r for a in sorted(ai) for r in self.T['ai'].get(a, [])]
        sk = {str(m.get('F4bQ7r8C') or '').split(':')[0] for m in g['U0v5IeJo']} - {''}
        g['8aiBoHg5'] = [self.T['skills'][s] for s in sorted(sk) if s in self.T['skills']]
        ps['pos'], ps['battle'] = str(point), bid
        g['yL3Yrf1M'] = [{'1kisc6IF': bid, 'Ez52fI60': '1'}]
        g['Kz7qfSs5'] = [{'k9cxD7Ba': bid, 'LE6JkUp7': '1| | '}]
        g['2cBk5WH8'] = [{'j28VNcUW': q['id'], 'qbJcuC60': str(point), 'tA4T0GXB': '1' if boss_id else ''}]
        g['9Q1Lq5FS'] = [{'h7eY3sAK': pl['user_id'], 'j28VNcUW': mission}]
        g['CEVQ4p7A'] = self.unit_rows(pl, ps)
        ps['last_battle'] = {'point': str(point), 'boss': boss_id}
        return g

    def unit_rows(self, pl, ps):
        ids = ps.get('unit_ids') or [r.get('edy7fq3L') for r in self.squad(pl)]
        return [pl['units'][u] for u in ids if u in pl['units']]

    # ------------------------------------------------------------------ helpers
    def helper_list(self, pl):
        F = self.S.friend_engine()
        tmpl = dict(self.T.get('helper_row') or {})
        mine = F.links.get(pl['user_id']) or {}
        ps = F.players()
        rows, n = [], 0
        friends = [o for o, st in mine.items() if st == 'friend' and o in ps]
        others = [o for o in ps if o != pl['user_id'] and o not in friends]
        random.shuffle(others)
        for o in friends + others[:max(0, 6 - len(friends))]:
            row = dict(tmpl)
            row.update(F._unit_fields(F.leader(ps[o])))
            row.update(F._person(ps[o]))
            row.update({'96Nxs2WQ': '1' if o in friends else '0', '0CAQ6wUe': str(int(time.time()))})
            rows.append(row)
            n += 1
        used = {r['B5JQyV8j'] for r in rows}
        while len(rows) < 8:
            h = F.npc_helper(pl, n, used)
            row = dict(tmpl)
            row.update({k: v for k, v in h.items() if k in row or k in ('h7eY3sAK', '98WfKiyA')})
            row.update({'96Nxs2WQ': '0', '0CAQ6wUe': str(int(time.time())), 'bM7RLu5K': ''})
            rows.append(row)
            n += 1
        pl['_raid_helpers'] = {r['h7eY3sAK']: r for r in rows}
        return rows

    # ------------------------------------------------------------------ rewards
    def finish(self, pl, room, q, ps):
        S = self.S
        st = self.state(pl)
        m = self.D['missions'][q['mission']]
        won = q['phase'] == 2 and ps and not ps.get('left')
        before = st['cls']
        first = q['mission'] not in st['cleared']
        rewards, boss_name, class_up = [], '', False
        if won:
            mult = S.admin_settings().get('mult', {})
            zel = int(m['zel'] * float(mult.get('zel', 1.0)))
            karma = int(m['karma'] * float(mult.get('karma', 1.0)))
            out = {}
            S.grant(pl, '3', '', zel, out)
            S.grant(pl, '2', '', karma, out)
            rewards += [('3', '', zel, '1'), ('11', '', karma, '1')]
            med = m.get('medal') or ''
            if ':' in med and _i(med.split(':')[1]) > 0:
                S.grant(pl, '12', med.split(':')[0], _i(med.split(':')[1]), out)
                rewards.append(('12', med.split(':')[0], _i(med.split(':')[1]), '1'))
            for item, n in ps['got']:
                S.add_item(pl, item, n)
                rewards.append(('4', item, n, '2'))
            if first:
                st['cleared'].append(q['mission'])
            if m.get('class_up') and m['cls'] == st['cls'] and st['cls'] < MAX_CLASS:
                st['cls'] += 1
                class_up = True
                for rtype, target, n in self.D['class_rewards'].get(str(st['cls']), []):
                    if rtype == '6':
                        for _ in range(max(1, n)):
                            S.add_present(pl, 6, target, 1, f'Raid Class {st["cls"]} reward')
                    else:
                        S.add_present(pl, rtype, '' if target == '0' else target, n, f'Raid Class {st["cls"]} reward')
                    rewards.append((rtype, target, n, '3'))
                S.log.info('raid: %s is now Raid Class %s', pl.get('handle'), st['cls'])
            boss_name = next((self.D['bosses'][b['boss']]['name'] for b in q['bosses']), '')
            sts = st['stats']
            sts['cleared'] = sts.get('cleared', 0) + 1
            S.log.info('raid: %s cleared %s: +%s zel, +%s karma, items %s', pl.get('handle'), m['name'], zel, karma, ps['got'])
        else:
            st['stats']['failed'] = st['stats'].get('failed', 0) + 1
        resp = {}
        resp['UC8S1AiX'] = [{'3fHunNe4': '1' if (won and first) else '0', '6dkEYp04': boss_name, 'oFQ3mbS6': '0'}]
        rows = []
        for t, target, n, src in rewards:
            r = self.tmpl('4sbU8kdW')
            r.update({'30Kw4WBa': t, 'TdDHf59J': str(target), 'wJsB35iH': str(n), '37moriMq': '', 'IkmC8gG2': src})
            rows.append(r)
        resp['4sbU8kdW'] = rows or [{'30Kw4WBa': '0', 'TdDHf59J': '', 'wJsB35iH': '0', '37moriMq': '', 'IkmC8gG2': '0'}]
        resp['awQFCW03'] = [{'UJTR1h5r': '1' if class_up else '0', '3Y9cKkuV': str(before)}]
        return resp

    def end_player(self, pl, room, q):
        ps = self.player(q, pl)
        if ps:
            ps['done'] = True
        info = room['info'].get(pl['user_id'])
        if info:
            info.update(status=0, ready=0)
        # everyone back in the room: the quest is over
        if all(p.get('done') or p.get('left') for p in q['players'].values()):
            self.quests.pop(q['id'], None)
            room['quest'], room['state'] = None, 0
            for i in room['info'].values():
                i.update(status=0, ready=0)

    # ------------------------------------------------------------------ requests
    def handle(self, pl, cls, req):
        with self.mu:
            try:
                return self._handle(pl, cls, req)
            except RaidError as e:
                return self.error(str(e))

    def _handle(self, pl, cls, req):
        S = self.S
        if not self.enabled():
            return self.error('Raid Battle is not open yet.')
        self.tidy()
        st = self.state(pl)
        self.profile(pl)
        name = cls[4:-7] if cls.endswith('Request') else cls
        room = self.room_of(pl, req)
        if room:
            room['seen'] = time.time()

        if name in ('UserInfo', 'Agreement', 'WorloInfo', 'GetPlayerInfo'):
            if name == 'Agreement':
                st['agreed'] = 1
            if name == 'GetPlayerInfo':
                row = {k: '0' for k in self.tmpl('6nqGvdY1')}
                row['h7eY3sAK'] = pl['user_id']
                return {'6nqGvdY1': [row]}
            resp = self.lobby(pl)
            if name == 'WorloInfo':
                resp.pop('1ry6BKoe')
                resp['taWh0Pr7'] = [{'43hMuY2I': '1', 'SyL0XFc6': str(len(self.where)), 'iFA0fd6Y': '1'}]
            if name == 'UserInfo':
                if st['bag']:
                    resp['Fihk73ZR'] = st['bag']
                if st['sub']:
                    resp['GvV1w4yM'] = st['sub']
            return resp

        if name == 'ShopUse':
            return self.shop(pl, room, req)

        if name in ('UpDateCash', 'UpdateBlackList', 'MissionSelect', 'UpScenarioInfo'):
            return {}                         # nothing kept for these (bfrevive answers them empty)

        if name == 'RoonItemEdit':
            self.set_bags(pl, _rows('Fihk73ZR', req), _rows('GvV1w4yM', req))
            return {}

        if name in ('UpDataRoomInfo', 'QuickStart'):
            r = _row('8iBuZa3n', req)
            mid = str(r.get('j28VNcUW') or (room or {}).get('mission') or '')
            if mid not in self.offered(pl):
                raise RaidError('This Raid quest is not available.')
            settings = {k: v for k, v in r.items() if k not in ('8VYd6xSX', '43hMuY2I', 'j28VNcUW')}
            if name == 'UpDataRoomInfo' and room and room['host'] == pl['user_id'] and str(r.get('8VYd6xSX') or ''):
                if room['state'] != 0:
                    raise RaidError('The quest has already started.')
                room['mission'] = mid
                room['settings'].update(settings)
            else:
                room = self.new_room(pl, mid, settings)
            if name == 'QuickStart':
                h = _row('ZX7gzQ6s', req)
                room['info'][pl['user_id']].update(helper=self.helper_pick(pl, h) if h else None, ready=1)
                out = self.room_groups(pl, room, units=False)
                out['1ry6BKoe'] = [self.user_row(pl)]
                if h:
                    out['ZX7gzQ6s'] = [room['info'][pl['user_id']]['helper']]
                return out
            out = self.room_groups(pl, room)
            out['1ry6BKoe'] = [self.user_row(pl)]
            return out

        if name == 'RoomList':
            mine = self.where.get(pl['user_id'])
            rows = [self.room_row(rm) for rm in self.rooms.values()
                    if rm['id'] != mine and rm['state'] == 0 and len(rm['members']) < min(MAX_MEMBERS, self.D['missions'][rm['mission']]['players'])
                    and self.D['missions'][rm['mission']]['cls'] <= st['cls']]
            return {'8iBuZa3n': rows}

        if name == 'Enter':
            rid = str(_row('8iBuZa3n', req).get('8VYd6xSX') or '')
            room = self.rooms.get(rid)
            if not room:
                raise RaidError('This Room is no longer available.')
            if pl['user_id'] not in room['members']:
                cap = min(MAX_MEMBERS, self.D['missions'][room['mission']]['players'])
                if len(room['members']) >= cap:
                    raise RaidError('This Room is full.')
                if room['state'] != 0:
                    raise RaidError('The quest in this Room has already started.')
                if self.D['missions'][room['mission']]['cls'] > st['cls']:
                    raise RaidError('Your Raid Class is too low for this quest.')
                self.leave(pl)
                room['members'].append(pl['user_id'])
                room['info'][pl['user_id']] = self.member_info()
                self.where[pl['user_id']] = rid
                S.log.info('raid: %s joined room %s', pl.get('handle'), rid)
            out = self.room_groups(pl, room)
            out['1ry6BKoe'] = [self.user_row(pl)]
            return out

        if name in ('Exit', 'RoomDissolve'):
            if room and name == 'RoomDissolve' and room['host'] == pl['user_id']:
                self.close_room(room)
            else:
                self.leave(pl)
            return self.lobby(pl)

        if name == 'RoomKick':
            who = str(_row('uwH62EZy', req).get('h7eY3sAK') or '')
            if room and room['host'] == pl['user_id'] and who in room['members'] and who != pl['user_id'] and room['state'] == 0:
                room['members'].remove(who)
                room['info'].pop(who, None)
                self.where.pop(who, None)
            if not room:
                raise RaidError('This Room is no longer available.')
            return self.room_groups(pl, room)

        if name in ('UpDataUserReady', 'FrlendSelect', 'RoomInfo', 'RoomFriendGet', 'SendChat', 'GetChatLog'):
            if not room or pl['user_id'] not in room['members']:
                raise RaidError('This Room is no longer available.')
            info = room['info'][pl['user_id']]
            if name == 'UpDataUserReady':
                info['ready'] = _i(_row('uwH62EZy', req).get('uX87tAZB'))
                return {'8iBuZa3n': [self.room_row(room)], 'uwH62EZy': self.member_rows(room)}
            if name == 'FrlendSelect':
                info['helper'] = self.helper_pick(pl, _row('ZX7gzQ6s', req))
                return {'8iBuZa3n': [self.room_row(room)], 'uwH62EZy': self.member_rows(room),
                        'ZX7gzQ6s': [info['helper']]}
            if name == 'RoomFriendGet':
                return {'ft4zY6Uv': self.helper_list(pl)}
            if name == 'SendChat':
                self.add_chat(pl, room, req)
                return {'WCA9VhL3': self.chat_since(room, req)}
            if name == 'GetChatLog':
                return {'WCA9VhL3': self.chat_since(room, req)}
            out = self.room_groups(pl, room)
            chat = self.chat_since(room, req)
            if chat:
                out['WCA9VhL3'] = chat
            return out

        # ---------------------------------------------------------------- in the quest
        if not room or pl['user_id'] not in room['members']:
            if name in ('MissionEnd', 'MissionExit'):
                return self.lobby(pl)
            raise RaidError('This raid room has closed.')
        q = self.quests.get(room.get('quest'))

        if name == 'MissionStart':
            if q is None:
                if room['host'] != pl['user_id']:
                    raise RaidError('The host has not started the quest yet.')
                if not self.supported(room['mission']):
                    raise RaidError('This Raid quest is not available.')
                if st['tickets'] <= 0:
                    raise RaidError('You have no Raid Battle entries left. One comes back every hour.')
                q = self.start_quest(room)
                S.log.info('raid: %s started quest %s (%s) in room %s with %s player(s)', pl.get('handle'), q['id'],
                           room['mission'], room['id'], len(room['members']))
            ps = self.player(q, pl)
            if not ps or ps.get('left') or ps.get('done'):
                if q['phase'] != 1:
                    raise RaidError('This quest is already over.')
                if time.time() - q['start'] > JOIN_SECONDS and pl['user_id'] != room['host']:
                    raise RaidError('It is too late to join this quest.')
                if st['tickets'] <= 0:
                    raise RaidError('You have no Raid Battle entries left. One comes back every hour.')
                room['info'][pl['user_id']]['status'] = 1
            ps = self.join_quest(pl, q, req)
            self.profile(pl)                 # entries left, as the room shows them
            out = self.quest_groups(pl, room, q)
            out['Fihk73ZR'] = st['bag']
            out['CEVQ4p7A'] = self.unit_rows(pl, ps)
            helper = room['info'][pl['user_id']].get('helper')
            if helper:
                out['ZX7gzQ6s'] = [helper]
            return out

        if q is None:
            if name in ('MissionEnd', 'MissionExit'):
                out = self.lobby(pl)
                out.update(self.room_groups(pl, room, units=False))
                return out
            raise RaidError('This quest is already over.')
        ps = self.player(q, pl)
        if ps is None:
            raise RaidError('You are not in this quest.')

        if name in ('MissionInfo', 'MissionInfoLight'):
            pos = _row('81B4TvEK', req).get('pjX27bKB')
            if pos and name == 'MissionInfo':
                ps['pos'] = str(pos)
            out = self.quest_groups(pl, room, q)
            chat = self.chat_since(room, req)
            if chat:
                out['WCA9VhL3'] = chat
            return out

        if name == 'MissionHarvest':
            point = str(_row('81B4TvEK', req).get('zHs17TE2') or '').split(':')[0]
            if not point:
                raise RaidError(HARVEST_EMPTY)
            ps['pos'] = point
            spot = (self.D['drops'].get(q['mission']) or {}).get(point) or {}
            done = ps['visited'].get(point, 0)
            if not spot.get('drops') or done >= max(1, spot.get('times') or 0):
                raise RaidError(HARVEST_EMPTY)
            rate, item, n = random.choices(spot['drops'], [max(1, d[0]) for d in spot['drops']])[0]
            ps['visited'][point] = done + 1
            self.add_got(ps, item, n)
            return self.quest_groups(pl, room, q)

        if name in ('CampRestStart', 'CampRestGet', 'CampRestEnd'):
            if name == 'CampRestStart':
                ps['camp'], ps['camp_t'] = 1, time.time()
            elif ps['camp']:
                ticks = int((time.time() - ps['camp_t']) // CAMP_TICK)
                if ticks > 0:
                    for u in ps['units']:
                        mx, hp = _i(u.get('3WMz78t6')), _i(u.get('e7DK0FQT'))
                        if 0 < mx and hp < mx:
                            u['e7DK0FQT'] = str(min(mx, hp + ticks * ((mx * CAMP_HEAL + 99) // 100)))
                    ps['camp_t'] += ticks * CAMP_TICK        # the part of a tick already rested counts on
                if name == 'CampRestEnd':
                    ps['camp'] = 0
            return self.quest_groups(pl, room, q)

        if name in ('LimitedUseItem', 'MissionUseItem'):
            use = _row('igAhC0R5', req)
            if use.get('kixHbe54'):
                self.take_bag_item(pl, use['kixHbe54'], _i(use.get('H6k1LIxC')) or 1)
            self.update_units(ps, req)
            out = self.quest_groups(pl, room, q)
            out['Fihk73ZR'] = st['bag']
            return out

        if name == 'ItemEdit':
            new = self.bag_rows(_rows('71U5wzhI', req))
            keep = {r['kixHbe54']: _i(r['wgV86x1q']) for r in new}
            old = {r['kixHbe54']: _i(r['wgV86x1q']) for r in st['bag']}
            for it in set(old) | set(keep):
                d = keep.get(it, 0) - old.get(it, 0)
                if d < 0:                    # out of the item set: into the bag of things carried
                    self.add_got(ps, it, -d)
                    S.warehouse_take(pl, it, -d)
                elif d > 0:                  # into the item set: only from the bag
                    took = self.take_got(ps, it, d)
                    if took:
                        S.add_item(pl, it, took)
                    if took < d:
                        for r in new:
                            if r['kixHbe54'] == it:
                                r['wgV86x1q'] = str(old.get(it, 0) + took)
                        new = [r for r in new if _i(r['wgV86x1q']) > 0]
            st['bag'] = new
            return {'Fihk73ZR': st['bag'], 'UsDq1E6o': self.got_rows(pl, ps)}

        if name in ('MissionBattleStart', 'MissionBattleRestart'):
            if q['phase'] != 1:
                raise RaidError('This quest is already over.')
            sel = _row('2cBk5WH8', req)
            point = str(sel.get('qbJcuC60') or ps['pos'])
            boss = str(sel.get('SP29fLtH') or '')
            if name == 'MissionBattleRestart' and ps.get('last_battle'):
                point, boss = ps['last_battle']['point'], ps['last_battle']['boss']
            g = self.battle(pl, q, ps, req, point, boss)
            out = self.quest_groups(pl, room, q)
            out.update(g)
            out['Fihk73ZR'] = st['bag']
            return out

        if name == 'UpdateProgress':
            self.update_units(ps, req)
            acks = self.apply_log(pl, q, ps, req)
            out = self.quest_groups(pl, room, q)
            if acks:
                out['QP4cwt7n'] = acks
            return out

        if name == 'MissionBattleEnd':
            self.update_units(ps, req)
            acks = self.apply_log(pl, q, ps, req)
            res = _row('zS9s5bEI', req)
            if ps['battle'] != '0':          # once per fight (a resent BattleEnd finds it closed)
                mine = [r for r in _rows('wc2DiMY1', req) if str(r.get('h7eY3sAK') or pl['user_id']) == pl['user_id']]
                if mine and all(_i(r.get('e7DK0FQT')) <= 0 for r in mine):
                    ps['lives'] = max(0, ps.get('lives', LIVES) - 1)
                    for u in ps['units']:
                        u.update({'e7DK0FQT': u.get('3WMz78t6', u.get('e7DK0FQT')), 'gU2xtQ0V': '0', 'nIGZ1X9C': '0'})
                    S.log.info('raid: %s was wiped out - %s heart(s) left', pl.get('handle'), ps['lives'])
                for part in str(res.get('4T0Q2Bh5') or '').split(','):     # items the fight dropped
                    it, _, n = part.partition(':')
                    if it.isdigit() and _i(n) > 0 and len(ps['got']) < BAG_STACKS:
                        self.add_got(ps, it, min(_i(n), 99))
            ps['battle'], ps['boss_battle'] = '0', 0
            sts = st['stats']
            sts['battles'] = sts.get('battles', 0) + 1
            if str(res.get('M4NAYyc6')) == '1':
                sts['won'] = sts.get('won', 0) + 1
            out = self.quest_groups(pl, room, q)
            if acks:
                out['QP4cwt7n'] = acks
            return out

        if name == 'MissionExit':
            ps['left'] = True
            self.end_player(pl, room, q)
            return {'8iBuZa3n': [self.room_row(room)], 'uwH62EZy': self.member_rows(room)}

        if name == 'MissionEnd':
            self.tick(q)
            if q['phase'] == 1:
                raise RaidError('The quest is not over yet.')
            if ps.get('done'):
                out = self.lobby(pl)
                out.update(self.room_groups(pl, room, units=False))
                return out
            out = self.finish(pl, room, q, ps)
            mrow = self.tmpl('g97TWISb')
            mrow.update({'j28VNcUW': q['mission'], '3tWuc7de': str(q['phase']), '8n7oG4fS': '0', 'd8IPTUp6': '', 'de9f4qXb': ''})
            out['g97TWISb'] = [mrow]
            self.end_player(pl, room, q)
            out.update({'8iBuZa3n': [self.room_row(room)], 'uwH62EZy': self.member_rows(room)})
            out.update(self.lobby(pl))
            out[S.G_TEAM] = [dict(S.team_row(pl['state']) or {})]
            out[S.G_BADGE] = {'dUujjBBK': S.achievement_engine().badges(pl)} if hasattr(S, 'achievement_engine') else {}
            return out

        if name == 'ItemMix':
            self.mix(pl, ps, req)
            return {'UsDq1E6o': self.got_rows(pl, ps)}

        if name == 'ItemDelete':
            rows = self.got_rows(pl, ps)
            for d in _rows('qa3iN58C', req):
                row = next((r for r in rows if r['n6E8iMf3'] == str(d.get('n6E8iMf3'))), None)
                if row and row['kixHbe54']:
                    self.take_got(ps, row['kixHbe54'], _i(d.get('wgV86x1q')) or 1)
            return {'UsDq1E6o': self.got_rows(pl, ps)}

        S.log.warning('raid: %s not handled yet', cls)
        raise RaidError('This is not available on this server yet.')

    def shop(self, pl, room, req):
        """RaidShopUse: 100 refills Raid entries (from the room screens), 101 refills hearts on the
        map. Gems are earned here, never bought, so spending them is fine; the price is the server's."""
        S = self.S
        st = self.state(pl)
        sel = _row('32ibWjFG', req)
        kind = _i(sel.get('60IsqxDt'))
        q = self.quests.get((room or {}).get('quest')) if room else None
        ps = self.player(q, pl) if q else None
        if kind == SHOP_LIVES and not ps:
            raise RaidError('You are not in a Raid quest.')
        full = st['tickets'] >= TICKETS if kind != SHOP_LIVES else ps.get('lives', LIVES) >= LIVES
        if not full:
            if int(pl['wallet'].get('gems', 0)) < SHOP_GEMS:
                raise RaidError('You do not have enough Gems.')
            pl['wallet']['gems'] = int(pl['wallet'].get('gems', 0)) - SHOP_GEMS
            if kind == SHOP_LIVES:
                ps['lives'] = LIVES
            else:
                st['tickets'], st['tick_t'] = TICKETS, 0
            S.log.info('raid: %s refilled %s for %s gem', pl.get('handle'),
                       'hearts' if kind == SHOP_LIVES else 'Raid entries', SHOP_GEMS)
        if kind == SHOP_LIVES:
            out = self.quest_groups(pl, room, q)
        else:
            out = self.lobby(pl)
            if room:
                self.profile(pl)
        out[S.G_TEAM] = [dict(S.team_row(pl['state']) or {})]
        return out

    def mix(self, pl, ps, req):
        """RaidItemMix: craft on the map from the bag of things carried - the town's recipes the player
        has opened, materials from the bag only, one item per craft, no karma (the game's own rule)."""
        S = self.S
        recipes = {str(r.get('4HqhTf3a')): r for r in S._town_mst('8f0bCciN') if isinstance(r, dict)}
        mine = {str(r.get('4HqhTf3a')) for r in S._rows(pl['state'].get(S.G_RECIPES)) if isinstance(r, dict)}
        for row in _rows('JTf2jY5o', req):
            for order in str(row.get('4HqhTf3a') or '').split(','):
                rid, _, times = order.partition(':')
                rec = recipes.get(rid)
                if not rec or rid not in mine:
                    continue
                for _ in range(max(1, min(99, _i(times) or 1))):
                    need = [(it, _i(c) or 1) for it, _, c in (p.partition(':') for p in str(rec.get('75YV2q1i') or '').split(',')) if it]
                    have = {e[0]: e[1] for e in ps['got']}
                    if len(ps['got']) >= BAG_STACKS or any(have.get(it, 0) < c for it, c in need):
                        break
                    for it, c in need:
                        self.take_got(ps, it, c)
                    self.add_got(ps, str(rec.get('kixHbe54')), 1)
                    S.log.info('raid: %s made %s on the map (recipe %s)', pl.get('handle'), rec.get('kixHbe54'), rid)

    def helper_pick(self, pl, h):
        """The helper chosen from RoomFriendGet (the list this server sent), as the game shows it."""
        uid = str(h.get('h7eY3sAK') or '')
        row = dict((pl.get('_raid_helpers') or {}).get(uid) or {})
        if not row:
            row = dict(self.T.get('helper_row') or {})
            row.update({k: str(v) for k, v in h.items()})
        row.update({'yu18xScw': '1', 'zsiAn9P1': '0'})
        return row

"""Shared loaders for the towns questline (read-only over out/questdata).  Run with `python -I`."""
import gzip
import json
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..'))
QD = os.path.join(REPO, 'out', 'questdata')

# quests assigned to the four towns by the coverage questgraph ("Side: Primm/Nipton/Novac/Boulder City")
CORE = {
    'Primm': ['VMS08', 'nVPrimmDeputyConv', 'nVPrimmVanceGun', 'VES04FactReactPrimmArea01PowderGangers'],
    'Nipton': ['VNipton', 'vNiptonVulpes'],
    'Novac': ['VMS19', 'VMS15', 'VMS11', 'VMS10', 'VMS23', 'VES01aVictorInNovac'],
    'Boulder City': ['VMS21', 'VMS21a', 'VMS20', 'VES01VictorGSMyBodyguard', 'VES01bVictorInBoulder'],
}
# quests not in a policy file / not in the questgraph's town line but located in these towns (found by EDID/name/script ref)
EXTRA = {
    'Primm': ['VMS08Timer', 'VES04FactionReactionPrimmArea01PowderGangers', 'vDialoguePrimm', 'VERPrimmDen01',
              'PrimmNashDialogue', 'vCasinoCompsVikkiAndVance', 'VDialogueVickyVance'],
    'Nipton': [],
    'Novac': ['vDialogueNovacGeneric', 'VDialogueNovac', 'VFreeformNovac', 'VERNovac01'],
    'Boulder City': ['VMS21aSupport', 'VFreeformBoulderCity', 'vDialogueBoulderCityGeneric', 'VES09BoulderCity'],
}
# quests with a policy section in docs/quest-policies/side-<town>.md
IN_POLICY_FILE = {'VMS08', 'nVPrimmDeputyConv', 'nVPrimmVanceGun', 'VNipton', 'VMS19', 'VMS15', 'VMS11', 'VMS23',
                  'VES01aVictorInNovac', 'VMS21', 'VMS21a', 'VMS20', 'VES01VictorGSMyBodyguard', 'VES01bVictorInBoulder'}


def all_quests():
    out = []
    for town in CORE:
        for e in CORE[town]:
            out.append((town, e, 'core'))
        for e in EXTRA[town]:
            out.append((town, e, 'extra'))
    return out


def jload(name):
    with open(os.path.join(QD, name), 'rb') as f:
        return json.loads(f.read().decode('utf-8'))


def gload(name):
    with gzip.open(os.path.join(QD, name), 'rb') as f:
        return json.loads(f.read().decode('utf-8'))


def load_infos():
    infos = defaultdict(list)
    with gzip.open(os.path.join(QD, 'infos.jsonl.gz'), 'rb') as f:
        for ln in f:
            i = json.loads(ln.decode('utf-8'))
            if i.get('quest'):
                infos[i['quest']].append(i)
    return infos


class Data:
    def __init__(self, qd=None):
        global QD
        if qd:
            QD = qd
        self.quests = jload('quests.json')
        self.by_edid = {q['edid']: q for q in self.quests['quests'] if q.get('edid')}
        self.by_ref = {q['ref']: q for q in self.quests['quests']}
        self.risk = jload('risk.json')['quests']
        self.infos = load_infos()
        self.effects = gload('effects.json.gz')
        self.native = gload('effects_native.json.gz')
        self.forms = gload('forms.json.gz')
        self.loadorder = json.load(open(os.path.join(REPO, 'tools', 'questdata', 'loadorder.json'), encoding='utf-8'))['plugins']
        self.graph = json.load(open(os.path.join(REPO, 'out', 'coverage', 'questgraph.json'), encoding='utf-8'))

    def runtime_id(self, ref):
        """'Plugin.esm:LOCAL' -> 8 hex digit runtime FormID using the shared load order."""
        plugin, local = ref.split(':')
        return '%02X%06X' % (self.loadorder.index(plugin), int(local, 16) & 0xFFFFFF)

    def form(self, ref):
        return self.forms.get(ref)


if __name__ == '__main__':
    d = Data()
    print(len(d.by_edid), 'quests', len(d.forms), 'forms')
    k = next(iter(d.forms.items()))
    print(k)

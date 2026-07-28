"""Check ammo data status."""
import json

files = ['ammo.json', '.222_remington_ammunition.json', '.300_aac_blackout_ammunition.json',
         '.45_acp_ammunition.json', '.sx_4.6x30_ammunition.json']
for f in files:
    try:
        d = json.load(open(f'data/{f}'))
        names = [x.get('name', '?') for x in d]
        print(f'{f}: {len(d)} items')
        for n in names:
            print(f'  - {n}')
        print()
    except Exception as e:
        print(f'{f}: ERROR - {e}\n')

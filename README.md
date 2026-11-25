# Battery Tray

Affiche en permanence la batterie de tes **appareils sans fil Logitech** (casque, souris, clavier…) sur la barre des tâches Windows, juste à gauche de la zone de notification :

```
 🎧⚡   🖱
 93%   78%      ^  🔊  20:14
```

- symbole de l'appareil en haut, pourcentage en dessous ;
- vert + éclair en charge, orange ≤ 30 %, rouge ≤ 15 %, gris quand l'appareil dort ou est éteint (dernière valeur connue) ;
- notification Windows à 20 % puis 10 % ;
- mise à jour toutes les 60 s, et immédiatement quand un câble est branché ou débranché ;
- se cache tout seul quand un jeu ou une vidéo est en plein écran ;
- fonctionne en même temps que G HUB.

## Installation

Dans PowerShell (pas besoin de droits administrateur ni de Python) :

```powershell
irm https://raw.githubusercontent.com/fbbbrand/battery-tray/main/install.ps1 | iex
```

L'appli est installée dans `%LOCALAPPDATA%\BatteryTray`, se lance tout de suite et à chaque ouverture de session. Relancer la même commande met à jour.

**Utilisation :** clic gauche sur le widget = rafraîchir, clic droit = menu (Rafraîchir / Quitter).

## Désinstallation

```powershell
irm https://raw.githubusercontent.com/fbbbrand/battery-tray/main/uninstall.ps1 | iex
```

## Appareils compatibles

Les appareils sont **détectés automatiquement** : le widget affiche une colonne par appareil trouvé (casque, souris, clavier, manette…) et se cache s'il n'y en a aucun.

| Protocole | Appareils concernés | Batterie lue via |
|---|---|---|
| **HID++ 2.0** | la plupart des souris, claviers et de nombreux casques Logitech sans fil récents, derrière un récepteur **Unifying, LIGHTSPEED ou Bolt** (jusqu'à 6 appareils par récepteur), ou branchés par câble | `0x1004` Unified Battery, `0x1000` Battery Status, `0x1001` Battery Voltage ou `0x1F20` ADC (tension convertie en %) |
| **Centurion** | casques récents comme le **PRO X 2 LIGHTSPEED** (via leur dongle) | sous-fonction `0x0104` derrière le pont `0x0003` |

**Testé** avec un casque PRO X 2 LIGHTSPEED et une souris PRO X3 SUPERSTRIKE (récepteur et câble). Les autres appareils devraient marcher s'ils parlent l'un de ces protocoles, mais ne sont pas encore vérifiés : si un appareil n'apparaît pas ou affiche une valeur bizarre, ouvre une *issue* en joignant `%LOCALAPPDATA%\BatteryTray\battery_tray.log`.

**Non compatibles :** les autres marques (Razer, SteelSeries, Corsair…), qui ont chacune leur propre protocole. Les appareils Logitech connectés en Bluetooth ne sont pas testés. Un appareil endormi au lancement de l'appli apparaît dès qu'il se réveille (à la lecture suivante, ≤ 60 s).

## Bon à savoir

- Certains antivirus signalent à tort les exécutables créés avec PyInstaller. Le code source complet est ici, et tu peux recompiler l'exe toi-même (voir plus bas).
- Le journal se trouve dans `%LOCALAPPDATA%\BatteryTray\battery_tray.log`.
- L'appli écoute sur `127.0.0.1:47821` (local uniquement) : une nouvelle instance s'en sert pour fermer l'ancienne.

## Compiler depuis les sources

Avec Python 3.12+ :

```powershell
.\build.ps1          # -> dist\BatteryTray.exe
```

Ou lancer sans compiler :

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\pythonw.exe app.py
```

| Fichier | Rôle |
|---|---|
| `devices.py` | découverte des appareils et lecture de la batterie (HID++, Centurion) |
| `widget.py` | le widget posé sur la barre des tâches |
| `app.py` | boucle de lecture, détection USB, notifications, instance unique |
| `icons.py`, `make_icon.py` | icônes (notification et exe) |
| `install.ps1`, `uninstall.ps1`, `build.ps1` | installation, désinstallation, compilation |

Protocoles documentés grâce au travail de reverse engineering des projets [HeadsetControl](https://github.com/Sapd/HeadsetControl) et [Solaar](https://github.com/pwr-Solaar/Solaar).

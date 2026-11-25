# Battery Tray

Affiche en permanence la batterie de ton **casque** et de ta **souris Logitech** sur la barre des tâches Windows, juste à gauche de la zone de notification :

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

Pour l'instant, seuls ces modèles sont reconnus (identifiants USB codés en dur dans [`devices.py`](devices.py)) :

| Appareil | Connexion | Protocole |
|---|---|---|
| Logitech **PRO X 2 LIGHTSPEED** (casque) | dongle sans fil `046D:0AF7` | Logitech « Centurion » (trames 64 octets, rapport `0x51`) |
| Logitech **PRO X3 SUPERSTRIKE** (souris) | récepteur LIGHTSPEED `046D:C54F` | HID++ 2.0, fonction `0x1004` (Unified Battery) |
| Logitech **PRO X3 SUPERSTRIKE** (souris) | câble USB `046D:C0A9` | HID++ 2.0 |

D'autres souris Logitech HID++ derrière un récepteur LIGHTSPEED ont de bonnes chances de marcher en ajoutant leur identifiant dans `HidppMouse.DEFAULT_ROUTES`. Le casque n'est lu que via son dongle (pas par le câble seul).

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
| `devices.py` | lecture de la batterie (HID++ pour la souris, Centurion pour le casque) |
| `widget.py` | le widget posé sur la barre des tâches |
| `app.py` | boucle de lecture, détection USB, notifications, instance unique |
| `icons.py`, `make_icon.py` | icônes (notification et exe) |
| `install.ps1`, `uninstall.ps1`, `build.ps1` | installation, désinstallation, compilation |

Protocole du casque inspiré du travail de reverse engineering du projet [HeadsetControl](https://github.com/Sapd/HeadsetControl).

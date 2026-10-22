# Attack Injection — Member A

## Functions

| Function | Target | Parameters |
| --- | --- | --- |
| `bias_attack` | Temperature | Fixed positive or negative offset, up to 10°C |
| `stuck_at_attack` | Motion | `frozen` for consecutive rows, or `single` for one changed row |
| `spoof_attack` | Temperature | Random replacement between 10°C and 50°C |
| `cmd_inject_attack` | Lock or fan | `lock`, `unlock`, or `safe_mode` |

`load_casas` selects one sensor and combines `date` and `time` into an ordered `timestamp`. If the file contains multiple homes, select one with `home_id`.

Sensor output keeps the CASAS columns and adds `timestamp` and `attack_type`. Normal rows use `normal`. The library functions keep motion in the CASAS `ON/OFF` format, but the generated motion sample files convert it to `true/false` to match `moves.py`. Command output contains the timestamp, component, topic, action, source, payload and attack label. The command source can be supplied as a function argument.

## Run

```bash
pip install -r requirements.txt
python create_samples.py --temperature casas_temperature_balanced.csv --motion casas_motion_balanced.csv --door casas_door_balanced.csv --temperature-sensor KitchenATemperature --motion-sensor Kitchen --door-sensor OutsideDoor --home-id mn61
```

The example uses the cleaned CASAS files and home `mn61`, which appears in all three. `mn77` also works. `--home-id` applies to all three files, so the chosen home must exist in each one. Omit `--home-id` if each selected stream already belongs to one home. The script uses the first 10 rows as the attack window; use `--start` and `--count` to choose another window.

The `mariam_samples` folder contains eight CSV files covering positive and negative bias, both Stuck-at versions for motion, temperature spoofing, and the three command actions. The bias and spoof files carry an extra `humidity` column set to the placeholder 48.0, matching the temperature and humidity pair that `moves.py` sends. `samples.json` lists the parameters, number of attacked rows and sample rows for each output. These files are generated when the script is run with the CASAS inputs. The original input files are preserved.

The door file is used only to time the lock command rows. Stuck-at targets motion only, because `moves.py` has no door or lock stuck-at move.

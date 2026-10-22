# Dataset Benchmarking — Member A

## My approach

I analysed Gotham 2025 and BCCC-IoT-IDS-Zwave-2025 to compare their size, missing values, attack types, class balance, and suitability for our MQTT smart-building testbed.

For Gotham, I read all 78 CSV files under `processed/` in `GothamDataset2025.zip`. For BCCC, I read all 40 CSV files in the dedicated `IP-files.zip` archive and all 206 CSV files in the dedicated `Z-wave-files.zip` archive. I read the complete files in chunks without sampling, dropping rows, filling missing values, or removing columns. I did not analyse JSON or PCAP records.

I kept IP flows, Z-Wave flows, and Z-Wave packets separate because they have different schemas and record types. Their row counts do not represent a single set of unique events. Gotham can also contain observations of the same exchange from different devices. These counts describe the CSV files I analysed, rather than every record described in the publishers' wider dataset descriptions.

## Measured results

| Measure | Gotham packets | BCCC IP flows | BCCC Z-Wave flows | BCCC Z-Wave packets |
|---|---:|---:|---:|---:|
| CSV files | 78 | 40 | 103 | 103 |
| Rows | 35,134,281 | 16,236,922 | 218,918 | 21,930,841 |
| Columns | 23 | 347 | 346 | 18 |
| Missing cells | 281,263,475 | 292,546,810 | 4,059,652 | 164,119,408 |
| Missing cells / all cells | 34.8060% | 5.1923% | 5.3596% | 41.5750% |
| Benign-labelled rows | 12,256,883 | 1,506 | 218,918 | Not available |
| Attack-labelled rows | 22,869,728 | 16,235,416 | 0 | Not available |
| Unknown-labelled rows | 7,670 | 0 | 0 | Not available |
| Unlabelled rows | 0 | 0 | 0 | 21,930,841 |
| Benign-to-known-attack ratio | 0.535944:1 | 0.000093:1 | Undefined: no attack-labelled rows | Unavailable: no label column |

Column counts include `label` where it is present. I counted `Benign` as normal traffic in Gotham and the BCCC Z-Wave flows. In BCCC IP flows, I counted both `Benign` and `Benign-with-mqtt` as normal. I excluded unknown and unlabelled rows from the benign-to-known-attack ratio.

The 40 measured BCCC IP files contain 29 attack labels and two benign labels. Benign rows account for 0.0093% of IP rows, while attack-labelled rows account for 99.9907%. The measured IP archive is strongly attack-heavy, so its class balance should not be treated as the normal operating balance of our testbed.

Every row in the 103 Z-Wave flow CSVs inside `Z-wave-files.zip` has the label `Benign`. The packet CSVs have no `label` column. I therefore cannot use these files alone to calculate a Z-Wave attack ratio or confirm specific Z-Wave attack classes. This does not mean that the wider BCCC collection contains no attacks. I have not replaced the observed labels with attack names from the publisher's general description.

## Additional attack names in the PCAP directory

After profiling the two dedicated CSV archives, I checked the separate `PCAPs/` directory. Its MQTT `Attacks.zip` has eleven named scenario folders: `connect-disconnect-flood`, `encoded-payload-flood`, `mqtt-bruteforce`, `ping-req-flood`, `publish-subscribe-flood`, `slow-publish`, `unvalid-publish-flood`, `unvalid-subscribe-flood`, `valid-publish-flood`, `very-large-message-flood`, and `wildcard-subscribe-flood`. These names identify folders containing PCAP files; they are not row labels in the two CSV archives profiled above.

The `Zwave-Hub-IP/` directory has archive groups named `Botnet`, `BruteForce`, `HTTP-based-DDoS`, `TCP-based-DDoS`, `Information-Gathering`, and `WebSocket`. Its separate `CSVs.zip` contains 340 CSV members. I checked sample rows from three of these CSVs and found an actual `label` column with `AVB-FiberFox`, `STRESS-FiberFox`, and `ACK-Flood-hping3`, respectively. In a different `csv-mqtt-z-wave-pcaps.zip` sample, the label was the broader `Z-wave`, which I did not count as a specific attack.

These supplementary CSVs use IP flow fields and were not included in the 246-file numerical profile or its attack counts, missing values, and class ratios. I have not profiled all their rows, and the folder names alone do not establish the publisher's full 24 Z-Wave classes. In particular, none of the inspected names confirms a sensor-value spoof, lock or fan actuation, or command replay in our MQTT testbed. I recorded the paths and inspected examples in `bccc_iot_ids_zwave_2025/pcap_attack_inventory.json`.

## Missing values and schema checks

I counted empty cells and the tokens `NA`, `N/A`, `NaN`, `nan`, `NULL`, `null`, and `?` as missing. For the Z-Wave packet files, I also counted `-` and `None`, which are used as absent-value markers. I did not treat all negative values as missing because fields such as RSSI can legitimately be negative.

Many packet fields are absent when they do not apply to that packet. In Gotham, all `ip.tos` cells are missing, and TCP or UDP fields are often empty for other protocols. These missing cells do not automatically indicate corrupted sensor readings. Missing-value percentages also depend on each schema and the stated missing-value definition.

The Z-Wave packet files use six column orders, with the same 18 column names. I matched missing-value totals by column name rather than position. I kept records with different column names in separate profiles. The exact missing count for every column is included in the result files.

## Coverage of our attack families

| Family | Gotham 2025 | BCCC CSVs analysed |
|---|---|---|
| Sensor spoofing | No explicit sensor-value spoofing label in the inspected inventory | No explicit sensor-value spoofing label in the IP inventory. Measured Z-Wave flows in `Z-wave-files.zip` are labelled only Benign, and its packet CSVs have no attack labels |
| Unauthorized actuation or replay | No explicit actuator-command injection or replay label. C&C labels alone do not establish a forged actuator command | No explicit actuator-command injection or replay label in the inspected IP inventory. Brute force and botnet labels do not establish unauthorized lock or fan actuation |
| Flooding | Covered by Mirai and Merlin flooding and CoAP amplification labels | Covered in IP flows by HTTP- and TCP-based flooding labels, including ACK, SYN and HTTP flooding. The released Z-Wave labels do not confirm flooding |

This comparison uses the actual CSV labels. It does not assume that a general network attack is also a physical sensor or actuator attack.

## Similarity to our MQTT testbed

Our testbed uses Mosquitto to exchange sensor readings and commands for a lock and fan. Gotham provides Ethernet, IP, TCP, and UDP packet information, so it is useful for network-layer analysis, especially flooding. I found 162,151 rows with `mqtt` in `frame.protocols`, which is 0.4615% of Gotham rows. This is a decoded-protocol count; encrypted or undecoded MQTT exchanges may not appear in that field.

The BCCC IP files contain flow statistics such as addresses, ports, packet counts, durations, rates, and TCP flags. The release includes a `Benign-with-mqtt` label, but its flow schema does not provide our MQTT topics, component states, or lock and fan commands. It can support network-level comparison, but I cannot use it as a direct replacement for our telemetry rows.

The Z-Wave files describe smart-home communication, including node identifiers, radio signal measurements, delivery information, and flow statistics. This is relevant to a smart-home setting, but Z-Wave is a different protocol from our MQTT pipeline. The packet payload field is not a ready-to-use sensor-state or actuator-command label. I therefore consider these files useful for comparison, with the labelling limits stated above. Neither released schema alone validates our physical-consistency layer.

## Gotham class counts

| Label | Rows |
|---|---:|
| Benign | 12,256,883 |
| C&C Communication | 528 |
| CoAP Amplification | 274,837 |
| File Download | 7,196 |
| Ingress Tool Transfer | 21,587 |
| Merlin C&C Communication | 29,356 |
| Merlin ICMP Flooding | 57,580 |
| Merlin TCP Flooding | 120,000 |
| Merlin UDP Flooding | 29,996 |
| Mirai C&C Communication | 1,074 |
| Mirai GRE Flooding | 5,911,401 |
| Mirai TCP Flooding | 6,548,173 |
| Mirai UDP Flooding | 8,897,895 |
| Reporting | 450 |
| TCP Scan | 737,764 |
| Telnet Brute Force | 227,649 |
| UDP Scan | 4,242 |
| Unknown | 7,670 |

## BCCC IP class counts

| Label | Rows |
|---|---:|
| ACK-Flood-hping3 | 820,000 |
| ACK-Flood-simple | 2,044,057 |
| APACHE-MHDDoS | 99,214 |
| BYPASS-MHDDoS | 2,420,346 |
| Benign | 651 |
| Benign-with-mqtt | 855 |
| Botnet-Hammer | 459,262 |
| Botnet-MHDDoS | 113,891 |
| BruteForce | 71,300 |
| BruteForce-Websocket | 354,063 |
| CFB-MHDDoS | 12,563 |
| DGB-MHDDoS | 181,252 |
| HEAD-MHDDoS | 94,874 |
| HTTP-Flood-Xerox | 12,243 |
| Information-Gathering | 452,717 |
| KILLER-MHDDoS | 59,217 |
| Mixed-Get-Post-Head | 537,161 |
| NULL-MHDDoS | 83,724 |
| NULL-TCP-Xerox | 307,187 |
| POST-Flood-MHDDoS | 207,603 |
| STOMP-MHDDoS | 92,815 |
| SYN-Flood-MHDDoS | 1,842,819 |
| SYN-Flood-Simple | 918,309 |
| SYN-PSH-URG-hping3 | 780,000 |
| SYN-PSH-hping3 | 2,298,274 |
| TCP-Connection-MHDDoS | 230,171 |
| TCP-Connection-Test-hping3 | 524,202 |
| TCP-Flood-FiberFox | 6,536 |
| TCP-Flood-MHDDoS | 4,529 |
| TCP-Flood-Xerox | 86,573 |
| TCP-Slowloris-hping3 | 1,120,514 |

The measured `Z-wave-files.zip` flow inventory contains only `Benign`: 218,918 rows. The Z-Wave packet files contain 21,930,841 unlabelled rows, so there is no packet attack-class table.

## Result files and repeatability

I saved aggregate results in each `profile.json`, class counts in `labels.csv`, per-file counts in `files.csv` and `file_profiles.json`, and per-file missing values in `missing_values.csv`. Each BCCC representation also has `missing_by_column.csv` for its aggregate column totals. The BCCC `source_manifest.csv` lists the 246 measured CSV members, their sizes, and their ZIP CRC values. The separate `pcap_attack_inventory.json` records the additional scenario names and sampled labels without mixing them into those measurements.

I read every CSV member completely through Python's ZIP reader, which checked its CRC. I did not compare whole original archive checksums with publisher checksums. The original multi-gigabyte datasets are not included in this submission; their download links and archive metadata are recorded in `sources.json`.

After downloading the original archives, I can repeat the analysis from this benchmarking folder with:

```bash
python profile_dataset.py GothamDataset2025.zip --dataset "Gotham 2025" --label label --benign Benign --prefix processed/ --output gotham_2025
python profile_dataset.py IP-files.zip --dataset "BCCC-IoT-IDS-Zwave-2025: IP flows" --label label --benign Benign Benign-with-mqtt --prefix IP-files/ --chunk-size 10000 --infer-types --output bccc_iot_ids_zwave_2025/ip_flows
python profile_dataset.py Z-wave-files.zip --dataset "BCCC-IoT-IDS-Zwave-2025: Z-Wave flows" --label label --benign Benign --prefix Z-wave-files/flows/ --chunk-size 10000 --output bccc_iot_ids_zwave_2025/zwave_flows
python profile_dataset.py Z-wave-files.zip --dataset "BCCC-IoT-IDS-Zwave-2025: Z-Wave packets" --label none --missing-token=- --missing-token=None --prefix Z-wave-files/packets-in-csv-format/ --chunk-size 50000 --output bccc_iot_ids_zwave_2025/zwave_packets
```

## Sources

- Project implementation: `README.md`, `firmware/smart_building_node.ino`, `backend/main.py`, and `Attacker/moves.py` in the team project.
- [Gotham dataset](https://zenodo.org/records/14502760)
- [Gotham paper](https://arxiv.org/abs/2502.03134)
- [BCCC dataset description](https://www.yorku.ca/research/bccc/ucs-technical/cybersecurity-datasets-cds/large-scale-iot-zwave-intrusion-detection-dataset-bccc-iot-ids-zwave-2025/)
- [BCCC download directory](http://bccc.laps.yorku.ca/BCCC-IoTIDS-ZWave-2025/CSVs/)
- [BCCC IP archive](http://bccc.laps.yorku.ca/BCCC-IoTIDS-ZWave-2025/CSVs/IP-files.zip)
- [BCCC Z-Wave archive](http://bccc.laps.yorku.ca/BCCC-IoTIDS-ZWave-2025/CSVs/Z-wave-files.zip)
- [BCCC MQTT attack PCAP archive](http://bccc.laps.yorku.ca/BCCC-IoTIDS-ZWave-2025/PCAPs/MQTT-HomeAssistant-servers-(switch-traffic)/Attacks.zip)
- [BCCC Z-Wave hub IP directory](http://bccc.laps.yorku.ca/BCCC-IoTIDS-ZWave-2025/PCAPs/Zwave-Hub-IP/)

I completed this CSV review on 22 October 2026.

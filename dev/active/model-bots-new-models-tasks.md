Parent: [[#152]] MODEL-BOTS-ON-NEW-MODELS-2026-09-25

# #152 tasks
- [ ] 1. Combined O/U model in production (`workers/model/combined_ou.py`, table `ou_model_predictions` + `combiner_ou_params`, fit in rating_1x2_shadow run, refresh every 30 min)
- [ ] 2. Un-retire `bot_v10_ou` on the combined O/U probability
- [ ] 3. Counterfactual + config family per model bot (pre-registered in the plan doc), split-confirm + Holm
- [ ] 4. Switch winners (new rule_version), docs (SYSTEM_MAP, registry, WHITEPAPER, WORKFLOWS), smoke

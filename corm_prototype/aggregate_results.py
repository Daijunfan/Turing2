from __future__ import annotations
import json
from pathlib import Path
from run_experiments import make_figures

ROOT=Path(__file__).resolve().parent
R=ROOT/'results'
keys=[
 'contract_space','random6_contracts','generic_compiler','exact_programs','scaling','fault_fraction','local_capacity_separation',
 'large_multiplier64','turnover_multiplier32','repeated_damage','stateful_vm','rule110'
]
all_results={k:json.loads((R/f'{k}.json').read_text()) for k in keys}
all_results['all_experiments_success']=all(
    (all(v.get('success',False) for v in result.values()) if k=='exact_programs' else result.get('success',False))
    for k,result in all_results.items()
)
(R/'summary.json').write_text(json.dumps(all_results,indent=2,sort_keys=True))
make_figures(all_results['scaling'],all_results['fault_fraction'])
print(json.dumps({
 'all_experiments_success':all_results['all_experiments_success'],
 'contract_functions':all_results['contract_space']['function_space'],
 'variants_verified':all_results['contract_space']['variants_verified'],
 'generic_compiler_cases':all_results['generic_compiler']['cases'],
 'generic_source_gates':all_results['generic_compiler']['total_source_gates'],
 'large_organs':all_results['large_multiplier64']['organs'],
 'large_active_cells':all_results['large_multiplier64']['active_cells_before'],
 'vm_cycles':all_results['stateful_vm']['cycles'],
 'rule110_cell_updates':all_results['rule110']['exact_cell_updates'],
},indent=2))

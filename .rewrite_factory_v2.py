from pathlib import Path
import json, re
root=Path('/home/boredompeaks/factory-v2')
# OpenCode host config: retain the three intended MCP servers and v2 defaults.
p=root/'opencode.json'; d=json.loads(p.read_text())
d['mcp']['servers']={k:v for k,v in d['mcp']['servers'].items() if k in {'basemind','chrome-devtools','context7'}}
d['plugin']=['./plugins/agt.js']
d['default_agent']='orchestrator'; d['experimental']['subagent_depth']=2
p.write_text(json.dumps(d,indent=2)+'\n')

# Agent personas: discard generated H/B contracts and typed permission expansions.
for p in (root/'agents').glob('*.md'):
    s=p.read_text()
    s=re.sub(r'(?m)^# -{5,}\n# HARNESS CONTRACT.*?^# -{5,}\n','',s,flags=re.S)
    s=re.sub(r'(?m)^permissions:\n.*?(?=^---\s*$)',
'''tools:
  - bash
  - read
  - write
  - edit
  - grep
  - glob
  - skill
  - webfetch
  - websearch
  - factory
''',s,flags=re.S)
    s=re.sub(r'(?m)^.*(?:Code Mode execute|Use typed tools only|Use the allowed tools only|Use typed tools plus|\*\*Carbon:\*\*).*\n','',s)
    s=s.replace('factory source','factory tree')
    s=s.replace('Read only `files/sop/', 'Read only `sop/')
    s=s.replace('`files/policies/', '`policies/')
    insertion='- Use shell and native tools (`bash`, `read`, `write`, `edit`, `grep`, `glob`, `skill`, `webfetch`, `websearch`) as needed.\n- The factory tree is read-only; assigned worktrees are writable within task scope.\n- Run checks and record evidence through the `factory` CLI.\n'
    # Insert where legacy orientation rules were removed, keeping role/skill instructions.
    marker='## Orientation\n\n'
    if marker in s:
        s=s.replace(marker,marker+insertion,1)
    p.write_text(s)

# AGT policy rebuilt with requested allowed surface and focused resource rules.
agt={
  'schemaVersion':1,'version':1,'mode':'enforce','denyOnPolicyError':True,
  'minimumPromptDefenseGrade':'medium',
  'additionalContext':'Factory tree is read-only. Worktrees are writable within task scope. Proofs are computed by the harness; release requires Proof-Root.',
  'toolPolicies':{
    'defaultEffect':'allow',
    'allowedTools':['read','glob','grep','list','bash','shell','write','edit','patch','multiedit','webfetch','websearch','skill','task','execute'],
    'blockedTools':[],
    'blockedToolCalls':[
      {'tool':'bash|shell|execute','pattern':'(^|[;&| ])curl\\b[^\n]*\\|[ ]*(sh|bash)\\b','reason':'Block downloading and executing scripts in one command.'},
      {'tool':'bash|shell|execute','pattern':'(169\\.254\\.169\\.254|metadata\\.google\\.internal)','reason':'Block cloud metadata service access.'},
      {'tool':'read|grep|glob|bash|shell|execute','pattern':'(^|/)(\\.env[^/]*|\\.ssh/|[^/]+\\.(pem|key))','reason':'Block direct reads of secrets and credentials.'},
      {'tool':'bash|shell|execute','pattern':'\\brm\\b[^\n]*(?:-r[^\n]*-f|-f[^\n]*-r|-rf|-fr)','reason':'Block recursive force deletion.'}
    ],
    'reviewTools':[]
  },
  'directResourcePolicies':{'pathRules':[
    {'id':'protected-source','effect':'deny','operation':'write','pathPatterns':[{'source':'^/home/boredompeaks/umairnlp/.*','flags':'i'},{'source':'^/home/boredompeaks/backend-worktrees/.*','flags':'i'}],'reason':'Source and permanent worktrees are read-only.'},
    {'id':'factory-policy-tree','effect':'deny','operation':'write','pathPatterns':[{'source':'(^|/)policies(/|$)','flags':'i'}],'reason':'The factory policy tree is read-only.'},
    {'id':'secrets','effect':'deny','operation':'read','pathPatterns':[{'source':'(^|/)\\.env[^/]*$','flags':'i'},{'source':'(^|/)\\.ssh(/|$)','flags':'i'}],'reason':'Secret files are not readable.'},
    {'id':'quality-ledger','effect':'deny','operation':'any','pathPatterns':[{'source':'(^|/)quality/ledger(/|$)','flags':'i'}],'reason':'Quality ledger paths are not directly accessible.'},
    {'id':'factory-read','effect':'allow','operation':'read','pathPatterns':[{'source':'(^|/)factory(/|$)','flags':'i'}],'reason':'Factory sources may be read.'}
  ]}
}
(root/'.agt/policy.json').write_text(json.dumps(agt,indent=2)+'\n')

# SOP typed command conversion, retaining step order and procedural content.
repls=[
 (r'check\.run\(\{\s*check_id\s*:\s*["\']([^"\']+)["\'][^}]*\}\)',r'`factory check \1`'),
 (r'check\.run\(\s*([A-Z][A-Z0-9_]+)\s*\)',r'`factory check \1`'),
 (r'check\.run\(\{[^}]*\}\)',r'`factory check <CHECK>`'),
 (r'git\.(state|status|log|diff|commit)\(\{[^}]*\}\)',r'`git \1`'),
 (r'git\.(state|status|log|diff|commit)\b',r'`git \1`'),
 (r'ledger\.append(?:\s+[a-z-]+)?',r'`factory evidence`'),
 (r'gate\.control\(\{[^}]*\}\)',r'`factory gate`'),
 (r'gate\.control\b',r'`factory gate`'),
 (r'pr\.open\([^)]*\)',r'`gh pr create`'),
 (r'worktrees\.sync(?:\(\{[^}]*\}\))?',r'`git`'),
 (r'harness\.verify\(\{[^}]*\}\)',r'`factory verify`'),
 (r'drain\.validate\b',r'`factory drain validate`'),
 (r'k1\.exec\{action:"([^"]+)"\}',r'`factory k1 \1`'),
 (r'k1\.up\(\{[^}]*\}\)',r'`factory k1 up`'),
 (r'k1\.down\b',r'`factory k1 down`'),
 (r'docker\.ensure\b',r'`factory services ensure`'),
 (r'repro\.run\b',r'`factory repro`'),
 (r'hacker\.probe\b',r'`factory probe`'),
 (r'git\.push_ref\(\{[^}]*\}\)',r'`git push`'),
]
for p in (root/'sop').rglob('*.md'):
    s=p.read_text()
    for a,b in repls: s=re.sub(a,b,s)
    s=s.replace('files/sop/','sop/').replace('files/policies/','policies/')
    s=re.sub(r'\bsealed\b','',s,flags=re.I)
    s=re.sub(r'\bunsealed\b','',s,flags=re.I)
    s=re.sub(r'\bcapability(?:\.[A-Za-z_]+)?\b','task context',s)
    s=re.sub(r'\bcapabilities\b','task contexts',s,flags=re.I)
    s=re.sub(r'\bseal(?:ed|ing)?\b','',s,flags=re.I)
    s=re.sub(r'\bledger\b','evidence store',s,flags=re.I)
    s=re.sub(r'\s+([,.])',r'\1',s)
    s=s.replace('proofs are recorded', 'proofs are computed by the harness')
    s=s.replace('proof refs','proof refs')
    # Remove old signature/capability properties and use task.json references.
    s=s.replace('sig_status/record_hash, ','').replace('sig_status, record_hash, ','')
    p.write_text(s)

# Runbooks that describe removed typed interfaces become short CLI pointers.
for name,body in {
 'gate.md':'# Gate\n\nUse `factory gate` to compute the gate set and proofs. Proofs are computed by the harness; release requires Proof-Root.\n',
 'ledger.md':'# Evidence\n\nThe legacy ledger interface is replaced by `factory evidence`. Proofs are computed by the harness; release requires Proof-Root.\n',
 'mcp.md':'# Factory CLI\n\nTyped MCP calls are replaced by the `factory` CLI. Use `factory check <CHECK>`, `factory gate`, and `factory evidence`. Proofs are computed by the harness; release requires Proof-Root.\n'
}.items(): (root/'runbooks'/name).write_text(body)

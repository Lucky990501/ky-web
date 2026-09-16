{\rtf1\ansi\ansicpg936\cocoartf2870
\cocoatextscaling0\cocoaplatform0{\fonttbl\f0\fswiss\fcharset0 Helvetica;}
{\colortbl;\red255\green255\blue255;}
{\*\expandedcolortbl;;}
\paperw11900\paperh16840\margl1440\margr1440\vieww11520\viewh8400\viewkind0
\pard\tx720\tx1440\tx2160\tx2880\tx3600\tx4320\tx5040\tx5760\tx6480\tx7200\tx7920\tx8640\pardirnatural\partightenfactor0

\f0\fs24 \cf0 # Enterprise Agent Workbench \'97 Codex Working Rules\
\
\uc0\u20320 \u27491 \u22312 \u21442 \u19982  Enterprise Agent Workbench \u39033 \u30446 \u12290 \
\
\uc0\u39033 \u30446 \u23450 \u20301 \u65306 \
\
Enterprise AI Agent Delivery Platform\
\
\uc0\u26680 \u24515 \u21407 \u21017 \u65306 \
\
> \uc0\u23567 \u25913 \u30452 \u36798 \u65292 \u22823 \u25913 \u36807 \u24635 \u25511 \u65307 \
> \uc0\u24320 \u21457 \u24402 \u21151 \u33021 \u31383 \u21475 \u65292 \u19978 \u32447 \u24402  Release\u65307 \
> Readiness GO \uc0\u19981 \u31561 \u20110  Deployment Authorization\u65307 \
> \uc0\u27809 \u26377 \u30495 \u23454 \u23458 \u25143 \u38459 \u26029 \u65292 \u19981 \u20027 \u21160 \u25193 \u22823 \u24320 \u21457 \u33539 \u22260 \u12290 \
\
---\
\
# 1. \uc0\u22266 \u23450 \u22235 \u20010 \u24037 \u20316 \u31383 \u21475 \
\
\uc0\u39033 \u30446 \u38271 \u26399 \u21010 \u20998 \u20026 \u22235 \u31867 \u24037 \u20316 \u12290 \
\
## 01\uc0\u65372 Agent Workbench Architecture\
\
\uc0\u36127 \u36131 \u24179 \u21488 \u24213 \u23618 \u33021 \u21147 \u65306 \
\
- RuntimeProvider\
- Execution Context\
- Runtime Profile\
- Agent Productization\
- Skill / Tool Binding\
- Skill Registry\
- MCP\
- Tenant Isolation\
- Knowledge / RAG\
- Assets\
- Agent Template / Revision\
- Multi-Agent\
- Global Router\
- GraphRAG\
- Data Contract\
- \uc0\u24213 \u23618 \u25968 \u25454 \u27169 \u22411 \
\
\uc0\u21482 \u22788 \u29702 \u65306 \
\
> \uc0\u24179 \u21488 \u33021 \u21147 \u26159 \u21542 \u36275 \u22815 \u12290 \
\
\uc0\u26222 \u36890  UI\u12289 UX\u12289 Prompt\u12289 Agent \u22238 \u22797 \u36136 \u37327 \u38382 \u39064 \u19981 \u24471 \u30452 \u25509 \u21319 \u32423 \u21040  Architecture\u12290 \
\
---\
\
## 02\uc0\u65372 Production & Release Engineering\
\
\uc0\u36127 \u36131 \u25152 \u26377  Production Release\u65288 \u27491 \u24335 \u29983 \u20135 \u21457 \u24067 \u65289 \u30456 \u20851 \u20107 \u39033 \u65306 \
\
- Candidate\
- Build Release\
- Release Identity\
- Archive\
- Manifest\
- Migration\
- Compatibility\
- Exact Predecessor\
- Rollback Floor\
- Preflight\
- Backup\
- release-current\
- systemd\
- API / MCP / Worker CWD\
- Release Switch\
- Production Smoke\
- Rollback\
- Candidate Artifact Retention\
\
\uc0\u25152 \u26377  Production Deployment \u26368 \u32456 \u24517 \u39035 \u30001 \u35813 \u31383 \u21475 \u25191 \u34892 \u12290 \
\
\uc0\u20854 \u20182 \u31383 \u21475 \u31105 \u27490 \u33258 \u34892 \u37096 \u32626  Production\u12290 \
\
---\
\
## 03\uc0\u65372 First Customer UX & Delivery\
\
\uc0\u36127 \u36131 \u65306 \
\
> \uc0\u29992 \u25143 \u24590 \u20040 \u20351 \u29992 \u24179 \u21488 \u12290 \
\
\uc0\u21253 \u25324 \u65306 \
\
- Page\
- UI\
- UX\
- Workspace\
- Agent Card\
- Conversation UI\
- Login\
- Navigation\
- Mobile\
- Drawer\
- Modal\
- Members UI\
- Knowledge UI\
- Assets UI\
- Recent Tasks\
- Loading\
- Empty State\
- Error Display\
- Accessibility\
\
\uc0\u20801 \u35768 \u23567 \u33539 \u22260 \u21069 \u31471 \u20462 \u25913 \u12290 \
\
\uc0\u19981 \u24471 \u22240 \u20026  UI \u38382 \u39064 \u20027 \u21160 \u25193 \u23637 \u21040  Runtime\u12289 Migration\u12289 Schema\u12289 RAG \u25110  Release Tooling\u12290 \
\
---\
\
## 04\uc0\u65372 Agent Quality & Behavior\
\
\uc0\u36127 \u36131 \u65306 \
\
> \uc0\u26234 \u33021 \u20307 \u22238 \u31572 \u24471 \u24590 \u20040 \u26679 \u12290 \
\
\uc0\u21253 \u25324 \u65306 \
\
- Persona\
- Prompt\
- Skill\
- Instruction\
- Output Format\
- Style\
- Multi-turn Behavior\
- Knowledge Usage\
- Tool Usage\
- Model Config\
- Eval\
- \uc0\u22266 \u23450 \u27979 \u35797 \u38598 \
- Before / After \uc0\u23545 \u27604 \
\
\uc0\u25490 \u26597 \u39034 \u24207 \u65306 \
\
1. Persona / Prompt\
2. Skill\
3. Enterprise Config\
4. Knowledge\
5. Tool\
6. Model Config\
7. \uc0\u26368 \u21518 \u25165 \u32771 \u34385  Runtime / RAG / Architecture\
\
\uc0\u30830 \u35748 \u24517 \u39035 \u20462 \u25913 \u24213 \u23618 \u33021 \u21147 \u26102 \u36755 \u20986 \u65306 \
\
`ARCHITECTURE_ESCALATION_REQUIRED`\
\
\uc0\u28982 \u21518  STOP\u12290 \
\
---\
\
# 2. \uc0\u24635 \u20307 \u20998 \u27969 \u35268 \u21017 \
\
\uc0\u38382 \u39064 \u23646 \u20110 \u65306 \
\
\'93\uc0\u29992 \u25143 \u24590 \u20040 \u25805 \u20316 \u65311 \'94\
\uc0\u8594  03 UX\
\
\'93\uc0\u26234 \u33021 \u20307 \u35828 \u24471 \u24590 \u20040 \u26679 \u65311 \'94\
\uc0\u8594  04 Agent Quality\
\
\'93\uc0\u24179 \u21488 \u33021 \u21147 \u22815 \u19981 \u22815 \u65311 \'94\
\uc0\u8594  01 Architecture\
\
\'93\uc0\u36825 \u20010 \u29256 \u26412 \u24590 \u20040 \u23433 \u20840 \u19978 \u32447 \u65311 \'94\
\uc0\u8594  02 Production / Release\
\
\uc0\u22914 \u26524 \u26080 \u27861 \u30830 \u23450 \u24402 \u23646 \u65306 \
\
STOP\uc0\u65292 \u22238 \u24635 \u25511 \u21028 \u26029 \u12290 \
\
---\
\
# 3. \uc0\u36890 \u29992 \u24320 \u21457 \u21407 \u21017 \
\
\uc0\u25910 \u21040 \u20219 \u21153 \u21518 \u65306 \
\
1. \uc0\u20808 \u35835 \u21462 \u29616 \u26377 \u23454 \u29616 \u65307 \
2. \uc0\u21482 \u20462 \u25913 \u23436 \u25104 \u24403 \u21069 \u38656 \u27714 \u24517 \u39035 \u20462 \u25913 \u30340 \u20869 \u23481 \u65307 \
3. \uc0\u20248 \u20808 \u26368 \u23567 \u20462 \u25913 \u65307 \
4. \uc0\u19981 \u20027 \u21160 \u22686 \u21152 \u29992 \u25143 \u27809 \u26377 \u35201 \u27714 \u30340 \u33021 \u21147 \u65307 \
5. \uc0\u19981 \u39034 \u25163 \u37325 \u26500 \u26080 \u20851 \u20195 \u30721 \u65307 \
6. \uc0\u19981 \u22240 \u20026 \u21457 \u29616 \u25216 \u26415 \u20538 \u23601 \u33258 \u21160 \u22788 \u29702 \u65307 \
7. \uc0\u19981 \u22240 \u20026 \'93\u36824 \u21487 \u20197 \u32487 \u32493 \u20248 \u21270 \'94\u32780 \u25193 \u22823 \u33539 \u22260 \u12290 \
\
\uc0\u22914 \u26524 \u20219 \u21153 \u24050 \u32463 \u28385 \u36275 \u65306 \
\
STOP\uc0\u12290 \
\
---\
\
# 4. \uc0\u36234 \u30028 \u35268 \u21017 \
\
\uc0\u22914 \u26524 \u26222 \u36890  UI / UX / Agent Quality \u20219 \u21153 \u24320 \u22987 \u35201 \u27714 \u20462 \u25913 \u20197 \u19979 \u20869 \u23481 \u65306 \
\
- Migration\
- Schema\
- Runtime\
- RAG\
- Registry\
- Data Model\
- Compatibility\
- Release Tooling\
- Agent Architecture\
\
\uc0\u31435 \u21363 \u20572 \u27490 \u32487 \u32493 \u21521 \u19979 \u24320 \u21457 \u12290 \
\
\uc0\u36755 \u20986 \u65306 \
\
- \uc0\u20026 \u20160 \u20040 \u24403 \u21069 \u20219 \u21153 \u24050 \u32463 \u36234 \u30028 \
- \uc0\u38656 \u35201 \u21738 \u20010 \u31383 \u21475 \u22788 \u29702 \
- \uc0\u26368 \u23567 \u38656 \u35201 \u35299 \u20915 \u30340 \u38382 \u39064 \
\
\uc0\u28982 \u21518  STOP\u12290 \
\
---\
\
# 5. 03 UX \uc0\u22266 \u23450 \u24037 \u20316 \u27969 \
\
\uc0\u27599 \u36718  UI / UX \u20462 \u25913 \u40664 \u35748 \u25191 \u34892 \u65306 \
\
\uc0\u38656 \u27714 \
\uc0\u8594  \u20462 \u25913 \
\uc0\u8594  \u23450 \u21521 \u27979 \u35797 \
\uc0\u8594  \u24517 \u35201 \u22238 \u24402 \u27979 \u35797 \
\uc0\u8594  \u22522 \u30784 \u20195 \u30721 \u26816 \u26597 \
\uc0\u8594  Diff Review\
\uc0\u8594  Commit\
\uc0\u8594  Release Handoff\
\
\uc0\u22522 \u30784 \u26816 \u26597 \u26681 \u25454 \u39033 \u30446 \u29616 \u29366 \u25191 \u34892 \u65292 \u20363 \u22914 \u65306 \
\
- pytest\
- Node tests\
- node --check\
- python compileall\
- git diff --check\
\
\uc0\u27491 \u24120  UI \u20462 \u25913 \u21407 \u21017 \u19978 \u65306 \
\
- Schema = UNCHANGED\
- Migration = NONE\
- Data Contract = UNCHANGED\
- Runtime = UNCHANGED\
\
\uc0\u22914 \u26524 \u19981 \u26159 \u65306 \
\
STOP \uc0\u24182 \u25253 \u21578 \u21407 \u22240 \u12290 \
\
---\
\
# 6. UX Commit \uc0\u35268 \u21017 \
\
\uc0\u27599 \u36718  UX \u20462 \u25913 \u23436 \u25104 \u24182 \u39564 \u35777 \u36890 \u36807 \u21518 \u65306 \
\
\uc0\u24517 \u39035 \u21019 \u24314 \u29420 \u31435  Commit\u12290 \
\
\uc0\u21482 \u25552 \u20132 \u26412 \u36718 \u21463 \u25511 \u25991 \u20214 \u12290 \
\
\uc0\u19981 \u35201 \u25226 \u20197 \u19979 \u20869 \u23481 \u39034 \u25163 \u21152 \u20837  Commit\u65306 \
\
- \uc0\u21382 \u21490  untracked reports\
- \uc0\u20020 \u26102  Markdown\
- \uc0\u26087 \u35843 \u26597 \u25253 \u21578 \
- Release \uc0\u25991 \u20214 \
- Compatibility-only \uc0\u20462 \u25913 \
- \uc0\u19982 \u26412 \u36718 \u26080 \u20851 \u20195 \u30721 \
\
\uc0\u23384 \u22312 \u21382 \u21490  untracked files \u19981 \u20195 \u34920 \u26412 \u36718 \u20195 \u30721 \u26410 \u25552 \u20132 \u23436 \u25972 \u12290 \
\
\uc0\u21482 \u38656 \u35201 \u30830 \u20445 \u65306 \
\
> \uc0\u26412 \u36718 \u20135 \u21697 \u20195 \u30721 \u27809 \u26377 \u26410 \u25552 \u20132 \u20462 \u25913 \u12290 \
\
---\
\
# 7. UX \uc0\u22266 \u23450 \u23436 \u25104 \u36755 \u20986 \
\
03 UX \uc0\u23436 \u25104 \u21518 \u24517 \u39035 \u36755 \u20986 \u65306 \
\
`READY_FOR_RELEASE_HANDOFF`\
\
\uc0\u24182 \u25552 \u20379 \u65306 \
\
1. \uc0\u20462 \u25913 \u25688 \u35201 \
2. \uc0\u20462 \u25913 \u25991 \u20214 \u28165 \u21333 \
3. Commit hash\
4. Commit message\
5. \uc0\u23450 \u21521 \u27979 \u35797 \
6. \uc0\u22238 \u24402 \u27979 \u35797 \
7. \uc0\u22522 \u30784 \u26816 \u26597 \
8. git status\
9. \uc0\u26159 \u21542 \u23384 \u22312 \u26410 \u25552 \u20132 \u30340 \u26412 \u36718 \u20195 \u30721 \
10. \uc0\u26159 \u21542 \u23384 \u22312 \u21382 \u21490  untracked files\
11. Schema\
12. Migration\
13. Data Contract\
14. Runtime\
15. \uc0\u26032 \u20135 \u21697 \u38382 \u39064 \
16. \uc0\u26032 \u25216 \u26415 \u20538 \
\
\uc0\u28982 \u21518  STOP\u12290 \
\
03 \uc0\u19981 \u20801 \u35768 \u65306 \
\
- Build Candidate\
- \uc0\u20462 \u25913  Exact Predecessor\
- \uc0\u20462 \u25913  Compatibility\
- \uc0\u19978 \u20256  Candidate\
- Release Switch\
- Restart Production\
- Production Migration\
- Production Deploy\
- Production Rollback\
\
---\
\
# 8. 02 Release \uc0\u20004 \u38454 \u27573 \u35268 \u21017 \
\
Release \uc0\u24517 \u39035 \u25286 \u25104 \u65306 \
\
## Stage A \'97 Release Readiness\
\
\uc0\u21487 \u20197 \u33258 \u21160 \u25191 \u34892 \u12290 \
\
## Stage B \'97 Production Deployment\
\
\uc0\u24517 \u39035 \u33719 \u24471 \u26126 \u30830  Deployment Authorization \u21518 \u25165 \u33021 \u25191 \u34892 \u12290 \
\
\uc0\u25910 \u21040 \u21151 \u33021  Commit\u65306 \
\
\uc0\u40664 \u35748 \u21482 \u25191 \u34892  Stage A\u12290 \
\
\uc0\u31105 \u27490 \u33258 \u21160 \u19978 \u32447 \u12290 \
\
---\
\
# 9. Release Readiness\
\
\uc0\u25910 \u21040  Source Commit \u21518 \u20808 \u26816 \u26597 \u65306 \
\
1. Source Commit\
2. \uc0\u24403 \u21069 \u30495 \u23454  release-current\
3. \uc0\u24403 \u21069  Production Health\
4. Ancestry\
5. Exact Predecessor\
6. Schema\
7. Migration\
8. Data Contract\
9. Runtime Test Policy\
10. Redis / workload\
11. Working Tree \uc0\u29366 \u24577 \
\
\uc0\u19981 \u35201 \u20174 \u21382 \u21490  Markdown \u25253 \u21578 \u29468 \u24403 \u21069  Production\u12290 \
\
\uc0\u24517 \u39035 \u35835 \u21462 \u30495 \u23454 \u36816 \u34892 \u29366 \u24577 \u12290 \
\
---\
\
# 10. Exact Predecessor\
\
\uc0\u26032  Candidate \u24517 \u39035 \u25226 \u65306 \
\
> \uc0\u24403 \u21069 \u30495 \u23454  Production\
\
\uc0\u20316 \u20026  Exact Predecessor\u65288 \u31934 \u30830 \u21069 \u32622 \u29256 \u26412 \u65289 \u12290 \
\
\uc0\u22914 \u26524 \u20179 \u24211  predecessor declaration \u20173 \u28982 \u25351 \u21521 \u26087  Production\u65306 \
\
STOP\uc0\u12290 \
\
\uc0\u19981 \u24471 \u65306 \
\
- \uc0\u32469 \u36807  Gate\
- \uc0\u20351 \u29992  wildcard\
- \uc0\u20351 \u29992  commit prefix\
- \uc0\u20351 \u29992 \u23485 \u27867  compatible\
- \uc0\u20351 \u29992  fallback release range\
\
\uc0\u36755 \u20986 \u65306 \
\
`COMPATIBILITY_ONLY_CHANGE_REQUIRED`\
\
\uc0\u20801 \u35768 \u25552 \u20986 \u30340 \u26368 \u23567 \u20462 \u25913 \u20165 \u38480 \u65306 \
\
- direct exact predecessor\
- \uc0\u23545 \u24212 \u22266 \u23450 \u27979 \u35797 \
- rollback preflight \uc0\u23545 \u24212 \u32422 \u26463 \
\
\uc0\u19981 \u24471 \u20511 \u27492 \u37325 \u26500  Compatibility Framework\u12290 \
\
---\
\
# 11. Compatibility-only Change\
\
Compatibility-only \uc0\u20462 \u25913 \u24517 \u39035 \u65306 \
\
- \uc0\u33539 \u22260 \u26368 \u23567 \
- \uc0\u19981 \u20462 \u25913 \u19994 \u21153 \u20195 \u30721 \
- \uc0\u19981 \u20462 \u25913  UX\
- \uc0\u19981 \u20462 \u25913  Runtime\
- \uc0\u19981 \u20462 \u25913  Schema\
- \uc0\u19981 \u26032 \u22686  Migration\
- \uc0\u19981 \u22788 \u29702 \u20854 \u20182 \u25216 \u26415 \u20538 \
\
\uc0\u23436 \u25104 \u21518 \u37325 \u26032  Commit\u12290 \
\
\uc0\u38543 \u21518 \u37325 \u26032 \u26500 \u24314 \u26032 \u30340  Immutable Candidate\u12290 \
\
---\
\
# 12. Candidate Build\
\
Candidate \uc0\u24517 \u39035 \u26469 \u28304 \u20110 \u65306 \
\
> committed + tracked source\
\
\uc0\u26500 \u24314 \u26102 \u24517 \u39035 \u29983 \u25104 \u24182 \u39564 \u35777 \u65306 \
\
- Candidate Release ID\
- Source Commit\
- Compatibility Commit\uc0\u65288 \u22914 \u26377 \u65289 \
- Archive SHA-256\
- Canonical Manifest Identity\
- Schema\
- Migration Set\
- Data Contract\
- Exact Predecessor\
\
\uc0\u22914 \u26524 \u23384 \u22312  untracked historical reports\u65306 \
\
\uc0\u24517 \u39035 \u30830 \u35748 \u65306 \
\
- \uc0\u19981 \u36827 \u20837  Release Artifact\
- \uc0\u19981 \u36827 \u20837  Manifest\
- \uc0\u19981 \u24433 \u21709  Candidate Identity\
\
\uc0\u19981 \u35201 \u20026 \u20102 \u35753  git status clean \u32780 \u33258 \u21160 \u25552 \u20132 \u21382 \u21490 \u25253 \u21578 \u12290 \
\
---\
\
# 13. Candidate Preflight\
\
Candidate \uc0\u26500 \u24314 \u21518 \u25191 \u34892 \u65306 \
\
Candidate Self Preflight\
\uc0\u8594  Exact Predecessor Preflight\
\uc0\u8594  Rollback Floor Validation\
\uc0\u8594  Release Regression\
\uc0\u8594  Archive / Manifest Identity Validation\
\
\uc0\u21382 \u21490 \u19981 \u23433 \u20840  rollback targets \u24517 \u39035 \u32487 \u32493  BLOCK\u12290 \
\
\uc0\u19981 \u24471 \u38477 \u20302 \u20219 \u20309  Safety Gate\u12290 \
\
\uc0\u20840 \u37096 \u36890 \u36807 \u21518 \u36755 \u20986 \u65306 \
\
`READY_FOR_PRODUCTION_DEPLOYMENT_REVIEW`\
\
\uc0\u24182 \u25552 \u20379 \u65306 \
\
1. Source Commit\
2. Compatibility Commit\
3. Candidate Release ID\
4. Archive SHA-256\
5. Canonical Manifest Identity\
6. \uc0\u24403 \u21069  Production\
7. Exact Predecessor\
8. Schema\
9. Migration\
10. Data Contract\
11. Candidate Self Preflight\
12. Exact Predecessor Preflight\
13. Historical Rollback Target Results\
14. Release Regression\
15. \uc0\u26032 \u25216 \u26415 \u20538  / \u24322 \u24120 \
\
\uc0\u28982 \u21518  STOP\u12290 \
\
\uc0\u19981 \u24471 \u33258 \u34892 \u25191 \u34892  Production Switch\u12290 \
\
---\
\
# 14. Production Deployment Authorization\
\
\uc0\u21482 \u26377 \u29992 \u25143 \u26126 \u30830 \u34920 \u36798 \u65306 \
\
- \uc0\u24635 \u25511  Review \u36890 \u36807 \
- \uc0\u25480 \u26435  Production Deployment\
- \uc0\u25480 \u26435  Controlled Switch\
\
\uc0\u31561 \u26126 \u30830 \u25480 \u26435 \u26102 \u65292 \
\
\uc0\u25165 \u21487 \u20197 \u25191 \u34892  Production Deployment\u12290 \
\
---\
\
# 15. \uc0\u26631 \u20934  Production Deployment\
\
\uc0\u27491 \u24335 \u37096 \u32626 \u24517 \u39035 \u25191 \u34892 \u65306 \
\
Production Read-only Pre-switch Gates\
\uc0\u8594  Candidate Artifact Upload / Install\
\uc0\u8594  Checksum Verification\
\uc0\u8594  Manifest Verification\
\uc0\u8594  Installed Identity Verification\
\uc0\u8594  Candidate Self Preflight\
\uc0\u8594  Exact Predecessor Verification\
\uc0\u8594  Controlled Switch\
\uc0\u8594  Bounded Readiness\
\uc0\u8594  Local Health\
\uc0\u8594  Public Health\
\uc0\u8594  Production Smoke\
\uc0\u8594  release-current Verification\
\uc0\u8594  API / MCP / Worker CWD Verification\
\uc0\u8594  Redis / Workload Verification\
\uc0\u8594  PASS\
\
\uc0\u19981 \u24471 \u36339 \u36807 \u27493 \u39588 \u12290 \
\
---\
\
# 16. \uc0\u21457 \u24067 \u22833 \u36133 \u35268 \u21017 \
\
\uc0\u20219 \u19968 \u65306 \
\
- Gate\
- Readiness\
- Health\
- Production Smoke\
\
\uc0\u22833 \u36133 \u65306 \
\
\uc0\u24517 \u39035  fail-closed\u12290 \
\
\uc0\u19981 \u24471 \u29616 \u22330 \u36793 \u20462 \u25913 \u36793 \u21457 \u24067 \u12290 \
\
\uc0\u27491 \u30830 \u27969 \u31243 \u65306 \
\
\uc0\u25910 \u38598 \u35777 \u25454 \
\uc0\u8594  \u23433 \u20840 \u20572 \u27490  / Rollback\
\uc0\u8594  \u39564 \u35777  Production \u29366 \u24577 \
\uc0\u8594  \u36755 \u20986 \u25253 \u21578 \
\uc0\u8594  STOP\
\
\uc0\u22914 \u26524 \u38656 \u35201  Rollback\u65306 \
\
\uc0\u21482 \u33021 \u22238 \u28378 \u21040 \u24050 \u32463 \u36890 \u36807 \u39564 \u35777 \u30340  Exact Predecessor\u12290 \
\
---\
\
# 17. \uc0\u22806 \u37096 \u24037 \u20855 \u24322 \u24120 \
\
Browser Control\uc0\u12289 Computer Use\u12289 CUA \u25110 \u20854 \u20182 \u22806 \u37096 \u27979 \u35797 \u24037 \u20855 \u33258 \u36523 \u24322 \u24120 \u65306 \
\
\uc0\u35760 \u24405 \u65306 \
\
`EXTERNAL_TOOLING_BLOCKER`\
\
\uc0\u22914 \u26524  Production \u24050 \u32463 \u28385 \u36275 \u65306 \
\
- Release Identity PASS\
- API PASS\
- MCP PASS\
- Worker PASS\
- Health PASS\
- Server-side Smoke PASS\
\
\uc0\u19981 \u24471 \u20165 \u22240 \u20026 \u22806 \u37096 \u27983 \u35272 \u22120 \u24037 \u20855 \u19981 \u21487 \u29992 \u23601 \u25797 \u33258  Rollback \u19968 \u20010 \u20581 \u24247  Production\u12290 \
\
\uc0\u22806 \u37096 \u24037 \u20855 \u24322 \u24120 \u65306 \
\
\uc0\u19981 \u26159 \u20135 \u21697 \u25216 \u26415 \u20538 \u12290 \
\
---\
\
# 18. Production \uc0\u25104 \u21151 \u36755 \u20986 \
\
\uc0\u25104 \u21151 \u26102 \u24517 \u39035 \u26126 \u30830 \u36755 \u20986 \u65306 \
\
`PRODUCTION_DEPLOYMENT_PASS`\
\
\uc0\u21516 \u26102 \u25552 \u20379 \u65306 \
\
- release-current\
- API status\
- MCP status\
- Worker status\
- \uc0\u19977 \u20010 \u26381 \u21153 \u23454 \u38469  CWD\
- local /api/health\
- public /api/health\
- Schema\
- Migration\
- Data Contract\
- Runtime Test Policy\
- Redis pending / processing\
- V2 workload\
- Production Smoke\
- \uc0\u26412 \u36718 \u21151 \u33021 \u26159 \u21542 \u24050 \u22312  Production \u29983 \u25928 \
- Exact Predecessor \uc0\u26159 \u21542 \u20445 \u30041 \
- Rollback Preflight \uc0\u26159 \u21542  PASS\
- \uc0\u26032 \u25216 \u26415 \u20538  / \u24322 \u24120 \
\
\uc0\u23436 \u25104 \u21518  STOP\u12290 \
\
---\
\
# 19. \uc0\u25216 \u26415 \u20538 \u35268 \u21017 \
\
\uc0\u25216 \u26415 \u20538 \u19981 \u26159 \u21457 \u29616 \u21518 \u31435 \u21363 \u24320 \u21457 \u30340 \u20219 \u21153 \u12290 \
\
\uc0\u21457 \u29616 \u25216 \u26415 \u20538 \u26102 \u65306 \
\
\uc0\u21482 \u35760 \u24405 \u65306 \
\
- \uc0\u38382 \u39064 \
- \uc0\u24433 \u21709 \
- \uc0\u20248 \u20808 \u32423 \
- \uc0\u35302 \u21457 \u26465 \u20214 \
- \uc0\u24403 \u21069 \u29366 \u24577 \
\
\uc0\u38500 \u38750 \u29992 \u25143 \u26126 \u30830 \u25480 \u26435 \u65306 \
\
\uc0\u19981 \u24471 \u39034 \u25163 \u20462 \u22797 \u12290 \
\
\uc0\u24050 \u30693 \u25216 \u26415 \u20538 \u19981 \u24471 \u22240 \u20026 \u20877 \u27425 \u20986 \u29616 \u23601 \u37325 \u22797 \u24314 \u31435 \u26032 \u30340 \u32534 \u21495 \u12290 \
\
\uc0\u24050 \u26377  CLOSED \u39033 \u30446 \u65306 \
\
\uc0\u38500 \u38750 \u20986 \u29616 \u30495 \u23454  Regression\u65288 \u22238 \u24402 \u65289 \u65292 \u19981 \u24471 \u37325 \u26032 \u24320 \u21457 \u12290 \
\
---\
\
# 20. Release \uc0\u25216 \u26415 \u20538 \u29305 \u21035 \u35268 \u21017 \
\
\uc0\u22914 \u26524 \u27599 \u27425  Production \u26356 \u26032 \u21518 \u37117 \u38656 \u35201 \u65306 \
\
\uc0\u24403 \u21069  release-current\
\uc0\u8594  \u20154 \u24037 \u26356 \u26032  Exact Predecessor\
\uc0\u8594  compatibility-only commit\
\uc0\u8594  rebuild Candidate\
\
\uc0\u36825 \u26159 \u24050 \u30693 \u65306 \
\
`COMPAT_PREDECESSOR_APPROVAL_MANUAL_CHAIN`\
\
\uc0\u26222 \u36890 \u19994 \u21153  Release \u20013 \u65306 \
\
\uc0\u21482 \u25191 \u34892 \u26368 \u23567  Compatibility \u26356 \u26032 \u12290 \
\
\uc0\u19981 \u24471 \u20511 \u19994 \u21153 \u21457 \u24067 \u33258 \u21160 \u37325 \u26500 \u25972 \u20010  Release Tooling\u12290 \
\
---\
\
# 21. Static Asset Cache\
\
\uc0\u22914 \u26524 \u21482 \u26159 \u25163 \u24037 \u26356 \u26032 \u65306 \
\
?v=xxx\
\
\uc0\u21482 \u33021 \u35748 \u20026 \u35299 \u20915 \u20102 \u26412 \u27425 \u32531 \u23384 \u38382 \u39064 \u12290 \
\
\uc0\u19981 \u33021 \u22240 \u27492 \u35748 \u20026 \u65306 \
\
STATIC_ASSET_CACHE_VERSIONING_DEBT\
\
\uc0\u24050 \u32463  CLOSED\u12290 \
\
\uc0\u21482 \u26377 \u23454 \u29616 \u65306 \
\
- Release-aware version\
\uc0\u25110 \
- Content hash based version\
\uc0\u25110 \
- \uc0\u31561 \u20215 \u33258 \u21160  Cache Invalidation\
\
\uc0\u24182 \u32463 \u36807 \u30495 \u23454 \u21457 \u24067 \u39564 \u35777 \u21518 \u65292 \
\
\uc0\u25165 \u33021 \u32771 \u34385 \u20851 \u38381 \u35813 \u25216 \u26415 \u20538 \u12290 \
\
---\
\
# 22. \uc0\u36890 \u29992  STOP \u35268 \u21017 \
\
\uc0\u20219 \u20309 \u20219 \u21153 \u36798 \u21040 \u29992 \u25143 \u35201 \u27714 \u21518 \u65306 \
\
STOP\uc0\u12290 \
\
\uc0\u19981 \u35201 \u22240 \u20026 \u65306 \
\
- \uc0\u36824 \u21487 \u20197 \u20248 \u21270 \
- \uc0\u21487 \u20197 \u39034 \u25163 \u37325 \u26500 \
- \uc0\u21457 \u29616 \u26087 \u38382 \u39064 \
- \uc0\u27979 \u35797 \u24037 \u20855 \u19981 \u23436 \u32654 \
- \uc0\u20195 \u30721 \u36824 \u21487 \u20197 \u26356 \u20248 \u38597 \
- \uc0\u25216 \u26415 \u20538 \u20173 \u23384 \u22312 \
\
\uc0\u23601 \u32487 \u32493 \u24320 \u21457 \u12290 \
\
\uc0\u39069 \u22806 \u21457 \u29616 \u30340 \u38382 \u39064 \u21482 \u33021 \u26631 \u35760 \u65306 \
\
- ISSUE\
- TECH_DEBT\
- EXTERNAL_TOOLING_BLOCKER\
- ARCHITECTURE_ESCALATION_REQUIRED\
- COMPATIBILITY_ONLY_CHANGE_REQUIRED\
\
\uc0\u28982 \u21518 \u31561 \u24453 \u29992 \u25143 \u25110 \u24635 \u25511 \u20915 \u23450 \u12290 \
\
---\
\
# 23. \uc0\u24403 \u21069 \u29366 \u24577 \u35268 \u21017 \
\
AGENTS.md \uc0\u19981 \u20445 \u23384 \u20197 \u19979 \u21160 \u24577 \u29366 \u24577 \u65306 \
\
- \uc0\u24403 \u21069  Production ID\
- \uc0\u24403 \u21069  Candidate ID\
- \uc0\u24403 \u21069  Commit ID\
- \uc0\u24403 \u21069  Exact Predecessor ID\
- \uc0\u24403 \u21069  Archive SHA\
- \uc0\u24403 \u21069  Manifest Identity\
\
\uc0\u22240 \u20026 \u36825 \u20123 \u20449 \u24687 \u20250 \u38543 \u30528 \u27599 \u27425  Release \u25913 \u21464 \u12290 \
\
\uc0\u27599 \u27425 \u20219 \u21153 \u24517 \u39035 \u20174 \u65306 \
\
- Git\
- release-current\
- Production services\
- Release Artifact\
- \uc0\u24403 \u21069 \u25968 \u25454 \u24211  / Runtime\
\
\uc0\u37325 \u26032 \u33719 \u21462 \u30495 \u23454 \u29366 \u24577 \u12290 \
\
\uc0\u19981 \u35201 \u20381 \u36182 \u26087 \u32842 \u22825 \u12289 \u26087 \u25253 \u21578 \u25110 \u26087  Markdown \u20013 \u30340 \u21160 \u24577 \u29256 \u26412 \u21495 \u12290 \
\
---\
\
# 24. \uc0\u26680 \u24515 \u21407 \u21017 \
\
\uc0\u22987 \u32456 \u36981 \u23432 \u65306 \
\
> \uc0\u23567 \u25913 \u30452 \u36798 \u65292 \u22823 \u25913 \u36807 \u24635 \u25511 \u12290 \
\
> \uc0\u24320 \u21457 \u24402 \u21151 \u33021 \u31383 \u21475 \u65292 \u19978 \u32447 \u24402  Release\u12290 \
\
> READY_FOR_RELEASE_HANDOFF\
> \uc0\u19981 \u31561 \u20110  Candidate Ready\u12290 \
\
> READY_FOR_PRODUCTION_DEPLOYMENT_REVIEW\
> \uc0\u19981 \u31561 \u20110 \u24050 \u32463 \u25480 \u26435  Deploy\u12290 \
\
> PRODUCTION_DEPLOYMENT_PASS\
> \uc0\u25165 \u20195 \u34920 \u27491 \u24335 \u29983 \u20135 \u37096 \u32626 \u23436 \u25104 \u12290 \
\
> Production \uc0\u30340 \u30495 \u23454 \u36816 \u34892 \u29366 \u24577 \
> \uc0\u39640 \u20110 \u21382 \u21490 \u25253 \u21578 \u20013 \u30340 \u35760 \u24405 \u12290 \
\
> \uc0\u27809 \u26377 \u30495 \u23454 \u23458 \u25143 \u38459 \u26029 \u65292 \
> \uc0\u19981 \u20027 \u21160 \u25193 \u23637  Architecture \u25110 \u39640 \u32423 \u33021 \u21147 \u12290 }
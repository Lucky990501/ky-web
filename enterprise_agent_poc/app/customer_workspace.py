"""Customer portal release scope; legacy Enabled is not migration evidence."""
CAPABILITY_CONTRACT = 'b1-customer-capabilities-v2'
KINDS = (('copywriting-agent','copywriting','文案创作'),('campaign-agent','campaign-planning','活动策划'),
         ('image-agent','image-generation','图片生成'),('wechat-agent','wechat-official-account-writing','公众号编写'))
MIGRATED = frozenset({'copywriting-agent'})


def capabilities(agents, *, runtime_configured):
    result=[]
    for identifier,slug,label in KINDS:
        agent=next((a for a in agents if a['id']==identifier or a.get('slug')==slug),None)
        enabled=bool(agent and agent.get('enabled'))
        migrated=identifier in MIGRATED
        if not migrated:state,reason='not_migrated','尚未完成正式业务迁移与验收'
        elif not enabled:state,reason='not_enabled','当前企业尚未开通'
        elif not runtime_configured:state,reason='unavailable','已开通，当前运行配置未就绪'
        else:state,reason='ready','已开通，满足本批运行准入'
        result.append({'id':agent['id'] if agent else identifier,'name':label,'enabled':enabled,
                       'migrated':migrated,'runnable':state=='ready','state':state,'reason':reason,
                       'contract':CAPABILITY_CONTRACT,'runtime_health':'not_probed'})
    return result

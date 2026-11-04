import { createHash } from 'node:crypto';

const md5 = text => createHash('md5').update(text, 'utf8').digest('hex');
export function authUrl(username, token, timestamp = Math.floor(Date.now()/1000)) {
  if (typeof username !== 'string' || !username.trim() || typeof token !== 'string' || !token || !Number.isSafeInteger(timestamp)) throw Error('configuration_invalid');
  const user = username.trim(), ut = String(timestamp);
  const code = md5(Buffer.from(md5(token).slice(4,23) + user + ut, 'utf8').toString('base64'));
  const url = new URL('wss://wsapi.17ce.com:8001/socket');
  url.searchParams.set('user',user); url.searchParams.set('code',code); url.searchParams.set('ut',ut);
  return url.href;
}

export function speedRequest(job) {
  const p=job.profile;
  const nums=(a, valid) => Array.isArray(a) && a.length>0 && a.length<=100 && a.every(x=>Number.isInteger(x)&&valid(x));
  if (!p || p.test_type!=='HTTP' || typeof job.target!=='string' || !nums(p.operators,x=>x>0&&x<=10000) || ![1,2,3].includes(p.area) || ![1,2,3,4,5].includes(p.node_type) || ![1,2,3].includes(p.nodes_per_region) || !Array.isArray(p.provinces) || !Array.isArray(p.cities) || (p.provinces.length && !nums(p.provinces,x=>x>0&&x<=10000)) || (p.cities.length && !nums(p.cities,x=>x>0&&x<=10000))) throw Error('profile_invalid');
  const request={txnid:1,nodetype:[p.node_type],num:p.nodes_per_region,Url:job.target,TestType:'HTTP',TimeOut:20,Request:'GET',NoCache:true,type:1,isps:[...p.operators],areas:[p.area]};
  if(p.provinces.length) request.pro_ids=p.provinces.join(',');
  if(p.cities.length) request.city_ids=p.cities.join(',');
  return request;
}

function summary(nodes,status,taskId,errorCode,allocated=null,submitted=false) {
  const operators={}; let success=0;
  for(const node of nodes.values()) {
    const key=node.operator, o=operators[key]??={node_count:0,success_count:0,failure_count:0,avg_seconds:null,max_seconds:null,_sum:0,_n:0};
    o.node_count++; if(node.ok){o.success_count++;success++}else o.failure_count++;
    if(node.seconds!==null){o._sum+=node.seconds;o._n++;o.max_seconds=o.max_seconds===null?node.seconds:Math.max(o.max_seconds,node.seconds)}
  }
  for(const o of Object.values(operators)){if(o._n)o.avg_seconds=o._sum/o._n;delete o._sum;delete o._n}
  const result={status,node_count:nodes.size,success_count:success,failure_count:nodes.size-success,operators,allocated_node_count:allocated,submitted};
  if(taskId)result.task_id=taskId;
  if(errorCode)result.error_code=errorCode;
  return result;
}

export async function runProvider(job, credentials, {socketFactory=url=>new WebSocket(url),begin,accepted,timeoutMs=240000}={}) {
  let socket, sent=false, permitted=false, taskId, allocated=null, countConflict=false, done=false, chain=Promise.resolve(); const nodes=new Map(), nodeOperators=new Map();
  let request,url;
  try { request=speedRequest(job); url=authUrl(credentials.username,credentials.api_token); }
  catch { return {...summary(nodes,'blocked',null,'configuration_invalid'),begun:false}; }
  return await new Promise(resolve=>{
    const finish=(status,code)=>{if(done)return;done=true;clearTimeout(timer);try{socket?.close()}catch{} resolve({...summary(nodes,status,taskId,code,allocated,sent),begun:permitted})};
    const timer=setTimeout(()=>finish(sent||permitted?'uncertain':'failed',sent||permitted?'provider_timeout':'provider_connect_failed'),Math.min(Math.max(timeoutMs,1),240000));
    try {socket=socketFactory(url)} catch {finish('failed','provider_connect_failed');return}
    let authenticated=false;
    socket.addEventListener('message',event=>{
      chain=chain.then(async()=>{
        if(done)return;
        let message; try {if(typeof event.data!=='string'||event.data.length>1024*1024)throw Error();message=JSON.parse(event.data)}catch{return}
        if(!authenticated){
          if(message.rt!==1){finish('blocked','provider_auth_failed');return}
          authenticated=true;
          let permit; try {permit=await begin()}catch{finish('failed','begin_unavailable');return}
          if(done)return;
          if(permit!==true){finish('blocked','permit_denied');return}
          permitted=true;
          if(socket.readyState!==1){finish('uncertain','provider_disconnected');return}
          sent=true;
          try{socket.send(JSON.stringify(request))}catch{finish('uncertain','provider_disconnected')}
          return;
        }
        if(message.txnid!==1)return;
        const data=message.data && typeof message.data==='object' && !Array.isArray(message.data)?message.data:{};
        const validTaskId=id=>(typeof id==='string'||typeof id==='number') && String(id).length<=200 && String(id).length>0 && !/[\x00-\x1f\x7f]/.test(String(id));
        if(message.type==='TaskErr' || message.type==='TaskEnd'){
          if(validTaskId(data.TaskId)){
            const incomingId=String(data.TaskId);
            if(taskId && taskId!==incomingId){allocated=null;countConflict=true}
            else taskId=incomingId;
          }
        }
        if(message.rt!==1 || message.type==='TaskErr'){
          finish(countConflict?'uncertain':'failed',countConflict?'provider_result_incomplete':'provider_task_failed');return
        }
        if(message.type==='TaskAccept'){
          const candidates=validTaskId(data.TaskId) ? [data] : Object.values(data).filter(x=>x && typeof x==='object' && !Array.isArray(x) && x.txnid===1 && validTaskId(x.TaskId));
          if(candidates.length>1){
            if(!taskId)taskId=String(candidates[0].TaskId);
            allocated=null;countConflict=true;return
          }
          if(candidates.length!==1)return;
          const receipt=candidates[0], incomingId=String(receipt.TaskId);
          if(taskId && taskId!==incomingId){allocated=null;countConflict=true;return}
          taskId=incomingId;
          if(Number.isInteger(receipt.nodeCount) && receipt.nodeCount>=0 && receipt.nodeCount<=10000){
            if(allocated!==null && allocated!==receipt.nodeCount){allocated=null;countConflict=true}
            else if(!countConflict)allocated=receipt.nodeCount;
          }
          if(receipt.nodes && typeof receipt.nodes==='object' && !Array.isArray(receipt.nodes)){
            for(const [nodeId,node] of Object.entries(receipt.nodes).slice(0,10000)){
              const isp=Number(node?.nodeinfo?.isp);
              if(nodeId.length<=100 && Number.isInteger(isp) && job.profile.operators.includes(isp))nodeOperators.set(nodeId,String(isp));
            }
          }
          if(taskId)try{await accepted(summary(nodes,'complete',taskId,undefined,allocated,sent))}catch{}
        } else if(message.type==='NewData'){
          if(validTaskId(data.TaskId)){
            const incomingId=String(data.TaskId);
            if(taskId && taskId!==incomingId)return;
            taskId=incomingId;
          }
          const id=data.NodeID;
          if((typeof id!=='number'&&typeof id!=='string') || String(id).length>100 || nodes.has(String(id)) || nodes.size>=10000)return;
          const raw=data.NodeInfo?.isp ?? data.NodeInfo?.ISP ?? data.NodeInfo?.Isp;
          const operator=nodeOperators.get(String(id)) ?? (Number.isInteger(Number(raw)) && job.profile.operators.includes(Number(raw)) ? String(Number(raw)) : 'unknown');
          const seconds=typeof data.TotalTime==='number' && Number.isFinite(data.TotalTime) && data.TotalTime>=0 && data.TotalTime<=240 ? data.TotalTime : null;
          nodes.set(String(id),{operator,seconds,ok:Number.isInteger(data.HttpCode)&&data.HttpCode>=200&&data.HttpCode<400});
        } else if(message.type==='TaskEnd'){
          const total=Number.isInteger(data.TotalCount) && data.TotalCount>=0 && data.TotalCount<=10000 ? data.TotalCount : null;
          if((allocated!==null && total!==null && allocated!==total) || (allocated!==null && nodes.size>allocated)){
            allocated=null;countConflict=true;
          }
          if(countConflict || (total!==null && nodes.size!==total) || (allocated!==null && nodes.size<allocated)){
            finish('uncertain','provider_result_incomplete');return
          }
          finish(nodes.size?'complete':'failed',nodes.size?undefined:'no_results');
        }

      }).catch(()=>finish(sent||permitted?'uncertain':'failed',sent||permitted?'provider_disconnected':'provider_connect_failed'));
    });
    socket.addEventListener('close',()=>{chain=chain.then(()=>finish(sent||permitted?'uncertain':'failed',sent||permitted?'provider_disconnected':'provider_connect_failed'))});
    socket.addEventListener('error',()=>{chain=chain.then(()=>finish(sent||permitted?'uncertain':'failed',sent||permitted?'provider_disconnected':'provider_connect_failed'))});
  });
}

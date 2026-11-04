import { pathToFileURL } from 'node:url';
import { randomUUID } from 'node:crypto';
import { encrypt, decrypt } from '../cloudflare/core.mjs';
import { runProvider } from './seventeence-provider.mjs';

const validId=id=>typeof id==='string' && /^[1-9][0-9]{0,29}-[1-9][0-9]{0,9}$/.test(id);
export class Service {
  constructor(base,token,key,fetcher=fetch) {
    try {
      const u=new URL(base);
      if(u.protocol!=='https:' || u.username || u.password || u.pathname!=='/' || u.search || u.hash || !token || /[\r\n]/.test(token) || !key)throw Error();
      this.base=u.origin; this.token=token; this.key=key; this.fetcher=fetcher;
    } catch {throw Error('configuration_invalid')}
  }
  async post(path,body){
    try {
      const response=await this.fetcher(this.base+path,{method:'POST',headers:{Authorization:'Bearer '+this.token,'Content-Type':'application/json'},body:JSON.stringify(body),redirect:'error',signal:AbortSignal.timeout(15000)});
      if(!response.ok)throw Error();
      const data=await response.json();if(!data || typeof data!=='object' || Array.isArray(data))throw Error();return data;
    } catch {throw Error('service_unavailable')}
  }
  async setup(){if((await this.post('/api/setup',{})).ok!==true)throw Error('service_invalid')}
  async claim(runId,executorId){
    if(!validId(runId) || typeof executorId!=='string' || !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(executorId))throw Error('configuration_invalid');
    const data=await this.post('/api/claim',{run_id:runId,executor_id:executorId});
    if(data.should_check===false)return null;
    try {
      if(data.should_check!==true || typeof data.payload!=='string')throw Error();
      const plan=await decrypt(data.payload,this.key,'17ce:plan:'+runId);
      if(plan.run_id!==runId || plan.executor_id!==executorId || typeof plan.lease_token!=='string' || !plan.credentials || !Array.isArray(plan.jobs) || plan.jobs.length>20)throw Error();
      return plan;
    }catch{throw Error('service_invalid')}
  }
  async begin(plan,job){const r=await this.post('/api/begin',{run_id:plan.run_id,executor_id:plan.executor_id,lease_token:plan.lease_token,job_id:job.id});if(typeof r.permit!=='boolean')throw Error('service_invalid');return r.permit}
  async event(plan,job,kind,result){
    const data={run_id:plan.run_id,executor_id:plan.executor_id,lease_token:plan.lease_token,job_id:job.id,event_id:job.id+':'+kind,kind,result};
    const payload=await encrypt(data,this.key,'17ce:event:'+plan.run_id);
    for(let i=0;i<3;i++){
      try{const response=await this.post('/api/event',{run_id:plan.run_id,payload});if(response.ok!==true)throw Error();return}
      catch{if(i===2)throw Error('service_unavailable')}
    }
  }
  async finish(plan){const r=await this.post('/api/finish',{run_id:plan.run_id,executor_id:plan.executor_id,lease_token:plan.lease_token});if(r.ok!==true)throw Error('service_invalid')}
}

export async function runClaim(service,runId,{provider=runProvider}={}) {
  const plan=await service.claim(runId,randomUUID());if(!plan)return 0;
  let count=0, failure=false;
  try {
    for(const job of plan.jobs){
      const result=await provider(job,plan.credentials,{begin:()=>service.begin(plan,job),accepted:r=>service.event(plan,job,'accepted',r)});
      const {begun,...payload}=result;
      if(payload.error_code!=='permit_denied'){
        try{await service.event(plan,job,'terminal',payload)}catch{failure=true;break}
        count++;
      }
      if(payload.error_code==='provider_auth_failed')break;
    }
  }finally{await service.finish(plan)}
  if(failure)throw Error('service_unavailable');
  return count;
}

async function main(){
  try {
    const mode=process.argv[2]; if(!['setup','run'].includes(mode))throw Error('configuration_invalid');
    const service=new Service(process.env.SEVENTEEN_API_URL,process.env.SEVENTEEN_RUNNER_API_TOKEN,process.env.SEVENTEEN_STATE_ENCRYPTION_KEY);
    if(mode==='setup'){await service.setup();console.log('setup_complete')}
    else{const count=await runClaim(service,process.env.ROUND_ID);console.log('run_complete: count='+count)}
  }catch(error){const allowed=new Set(['configuration_invalid','service_unavailable','service_invalid']);console.error('seventeence_error: '+(allowed.has(error?.message)?error.message:'operation_failed'));process.exitCode=1}
}
if(process.argv[1] && import.meta.url===pathToFileURL(process.argv[1]).href)await main();

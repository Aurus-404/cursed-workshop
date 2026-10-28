const stopped=()=>new DOMException('Operation cancelled','AbortError');

// The POST is not aborted: retain the returned ID so the server can stop its job.
export async function runJob(api,path,body,{signal,onStatus=()=>{},wait=ms=>new Promise(r=>setTimeout(r,ms)),interval=250}={}){
  if(signal?.aborted)throw stopped();
  const {job_id}=await api(path,body);
  let cancellation;
  const cancel=()=>{cancellation??=api('/api/cancel-job',{id:job_id}).catch(()=>({accepted:false}));};
  signal?.addEventListener('abort',cancel,{once:true});if(signal?.aborted)cancel();
  try{
    for(;;){
      const job=await api('/api/job?id='+encodeURIComponent(job_id));onStatus(job);
      if(job.status==='cancelled'||job.status==='superseded')throw stopped();
      if(job.status==='error')throw new Error(job.error);
      if(job.status==='done'){
        if(signal?.aborted&&job.cancellable)throw stopped();
        return job.result;
      }
      await wait(interval);
    }
  }catch(error){
    // A failed poll must not leave a supersedable preview occupying the queue.
    // The server is the authority: protected publication jobs refuse cancellation.
    cancel();throw error;
  }finally{signal?.removeEventListener('abort',cancel);onStatus({status:'finished',cancellable:false});}
}

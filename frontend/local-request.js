export async function localRequest(path,body,token,{signal}={}) {
  const method=body?'POST':'GET',route=String(path).split('?')[0];
  let response;
  for(let attempt=0;attempt<2;attempt++){
    try{response=await fetch(path,{method,signal,headers:{'X-Workshop-Token':token,...(body?{'Content-Type':'application/json'}:{})},...(body?{body:JSON.stringify(body)}:{})});break;}
    catch(error){
      if(error.name==='AbortError')throw error;
      if(method==='GET'&&attempt===0){await new Promise(resolve=>setTimeout(resolve,150));if(signal?.aborted)throw new DOMException('Preview superseded','AbortError');continue;}
      throw new Error(`Local request failed (${method} ${route}): ${error.message||'connection unavailable'}`);
    }
  }
  const data=await response.json();
  if(!response.ok)throw new Error(data.error||'The local request could not be completed.');
  return data;
}

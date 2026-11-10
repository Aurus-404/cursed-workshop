// In browser mode the page outlives the local server: it stays open after the
// tool ended, and every button would fail with a network error. Say what happened.
export function serverWatch({probe,notify,wait=ms=>new Promise(resolve=>setTimeout(resolve,ms))}) {
  let lost=false;
  const set=value=>{if(value!==lost){lost=value;notify(value);}};
  return {
    alive(){set(false);},
    // After a failed request: is the server gone, or was that one call unlucky?
    async confirm(){
      for(let attempt=0;attempt<2;attempt++){
        if(attempt)await wait(1000);
        try{await probe();set(false);return false;}catch{}
      }
      set(true);return true;
    },
  };
}

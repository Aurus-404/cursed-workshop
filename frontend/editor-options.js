export const DEFAULT_BINDINGS={move:'G',rotate:'R',space:'Q',frame:'F',hide:'H',isolate:'Shift+H',show:'Alt+H',lock:'L',undo:'Ctrl+Z',redo:'Ctrl+Y',save:'Ctrl+S',cancel:'Escape',search:'Ctrl+F',help:'F1',snap_hold:'Control',fine_hold:'Shift'};
export function editorOptions(value={}){return {autosave:value.autosave!==false,snap:value.snap===true,move_step:positive(value.move_step,1),angle_step:Math.min(360,positive(value.angle_step,15)),fine_factor:Math.min(1,positive(value.fine_factor,.1)),bindings:{...DEFAULT_BINDINGS,...value.bindings}};}
const positive=(value,fallback)=>Number.isFinite(value)&&value>=.001&&value<=10000?value:fallback;
export function chord(event){const key=event.key.length===1?event.key.toUpperCase():event.key;return [...(event.ctrlKey&&key!=='Control'?['Ctrl']:[]),...(event.altKey&&key!=='Alt'?['Alt']:[]),...(event.shiftKey&&key!=='Shift'?['Shift']:[]),...(event.metaKey&&key!=='Meta'?['Meta']:[]),key].join('+');}
export function held(binding,event){return binding==='Control'?event.ctrlKey:binding==='Shift'?event.shiftKey:binding==='Alt'?event.altKey:binding==='Meta'?event.metaKey:false;}
export function snapping(control,options,event={}){
  const fine=held(options.bindings.fine_hold,event),enabled=options.snap||held(options.bindings.snap_hold,event),factor=fine?Math.min(options.fine_factor,1):1;
  control.setTranslationSnap(enabled||fine?options.move_step*factor:null);
  control.setRotationSnap(enabled||fine?options.angle_step*factor*Math.PI/180:null);
  control.setScaleSnap((enabled||fine)? .1*factor:null);
}

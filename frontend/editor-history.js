// Snapshots stay within the current tool session, so undo never edits a closed tool.
export function editorHistory(limit=100){
  let undo=[],redo=[],floor=0;
  return {
    begin(){floor=undo.length;redo=[];},
    clear(){undo=[];redo=[];floor=0;},
    remember(value){if(undo.at(-1)!==value)undo.push(value);if(undo.length>limit){undo.shift();floor=Math.max(0,floor-1);}redo=[];},
    available(direction){return direction==='undo'?undo.length>floor:redo.length>0;},
    step(direction,current){
      const from=direction==='undo'?undo:redo,to=direction==='undo'?redo:undo,min=direction==='undo'?floor:0;
      while(from.length>min&&from.at(-1)===current)from.pop();
      if(from.length<=min)return null;
      to.push(current);return from.pop();
    },
  };
}

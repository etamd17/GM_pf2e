// UI navigation only. No table command is issued by a scene change.
export function editingSceneUrl(current:string,sceneId:string | null):string {
  const url=new URL(current);
  if(sceneId) url.searchParams.set('sceneId',sceneId);
  else url.searchParams.delete('sceneId');
  return url.toString();
}
export function navigateToScene(sceneId:string | null):void {
  window.location.assign(editingSceneUrl(window.location.href,sceneId));
}

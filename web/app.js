const $ = id => document.getElementById(id);
let lastRecord = null, lastHistory = '', lastState = null;
let toastTimer;
const root = document.documentElement;
const savedTheme = localStorage.getItem('peleng-theme');
root.dataset.theme = savedTheme || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
$('themeToggle').onclick=()=>{root.dataset.theme=root.dataset.theme==='dark'?'light':'dark';localStorage.setItem('peleng-theme',root.dataset.theme);if(lastState)draw(lastState.path||[]);};
function setSystemStatus(text,online=false){const el=$('systemStatus');el.innerHTML='<i></i>'+text;el.classList.toggle('online',online);}
function toast(text, error = false) {
  const el=$('toast'); el.textContent=text; el.className='toast visible'+(error?' error':'');
  clearTimeout(toastTimer); toastTimer=setTimeout(()=>el.className='toast',4800);
}
async function api(path, body) {
  const r=await fetch(path,{method:body===undefined?'GET':'POST',headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),cache:'no-store'});
  const data=await r.json().catch(()=>({}));
  if(!r.ok)throw new Error(data.detail || 'HTTP '+r.status);
  return data;
}
function setText(id,t){$(id).textContent=t;}
function number(n,suffix='',digits=3){return typeof n==='number'?n.toFixed(digits)+suffix:'—';}
function chip(id,live){$(id).textContent=live?'В ЭФИРЕ':'НЕТ СИГНАЛА';$(id).classList.toggle('live',!!live);}
async function refresh(){
 try{
  const s=await api('/api/dashboard');lastState=s;
  chip('macChip',s.mac_online);chip('iphoneChip',s.iphone_online);
  const ready=s.mac_online&&s.iphone_online;
  setSystemStatus(ready?'Две камеры в эфире':'Ожидание камер',ready);
  setText('macFps',number(s.mac_fps,' FPS',1));setText('iphoneFps',number(s.iphone_fps,' FPS',1));
  setText('address',`http://${s.lan_ip}:${s.port}`);setText('pin',s.pin);
  setText('pairCount',s.calibration_pairs);setText('matched',s.matched_pairs);setText('dropped',s.dropped_pairs);
  setText('sync',number(s.sync_error_ms,' ms',1));setText('uncertainty',number(s.clock_uncertainty_ms,' ms',1));
  setText('baseline',number(s.baseline_m,' m'));setText('rms',number(s.calibration_rms_px,' px'));
  setText('recCount',s.samples_recorded+' точек');
  setText('calibstat',s.calibrated?`Профиль активен · RMS ${number(s.calibration_rms_px,' px')}`:'Калибровка не готова для текущей пары камер');
  const v=s.measurement;
  for(const [id,key] of [['x','x_m'],['y','y_m'],['z','z_m'],['errorPx','reprojection_error_px']])setText(id,v?number(v[key]):'—');
  setText('measureTag',v?'ПОЗИЦИЯ ПОЛУЧЕНА':!s.mac_online?(s.mac_error||'Нет Mac-камеры'):!s.iphone_online?'Ожидание iPhone':s.error||'НЕТ ИЗМЕРЕНИЙ');$('measureTag').classList.toggle('valid',!!v);
  $('recordStart').disabled=!s.calibrated||s.recording;$('recordStop').disabled=!s.recording;
  $('recordStart').textContent=s.recording?'Запись идёт':'Начать запись';
  draw(s.path||[]);
  if(lastRecord!==s.recording){lastRecord=s.recording;loadHistory();}
 }catch(e){setSystemStatus('Сервер недоступен');}
}
function refreshImages(){const time=Date.now();for(const id of ['mac','iphone']){const image=$(id+'Preview'),online=lastState&&lastState[id+'_online'];$(id+'Offline').style.display=online?'none':'block';if(online)image.src='/api/preview/'+id+'?t='+time;else{image.removeAttribute('src');image.style.opacity='0';}}}
async function loadHistory(){try{const files=await api('/api/history');const text=JSON.stringify(files);if(lastHistory===text)return;lastHistory=text;
 $('history').innerHTML='';if(!files.length){$('history').textContent='Записей пока нет.';return;}
 for(const file of files){const a=document.createElement('a');a.href='/api/history/'+encodeURIComponent(file.name);a.download=file.name;a.textContent='↓ '+file.name+' ('+file.size+' B)';$('history').appendChild(a);}
}catch(e){toast(e.message,true);}}
async function postClick(button,fn){button.disabled=true;try{const reply=await fn();toast(reply||'Операция выполнена');await refresh();}catch(e){toast(e.message,true);}finally{button.disabled=false;}}
$('capture').onclick=()=>postClick($('capture'),async()=>{const r=await api('/api/calibration/capture',{});return `Сохранена пара ${r.pairs}. Покажи доску в другом положении.`;});
$('calibrate').onclick=()=>postClick($('calibrate'),async()=>{const size=Number($('square').value);const r=await api('/api/calibration/run',{square_m:size});return `Калибровка: RMS ${r.calibration.rms.toFixed(3)} px; база ${r.calibration.baseline_m.toFixed(3)} м`;});
$('reset').onclick=()=>{if(!confirm('Удалить все сохранённые снимки калибровки? Существующий профиль сохраняется.'))return;postClick($('reset'),async()=>{await api('/api/calibration/reset',{});return 'Снимки удалены';});};
$('recordStart').onclick=()=>postClick($('recordStart'),async()=>{await api('/api/record/start',{});return 'Запись началась. Будут сохранены только измеренные 3D-точки.';});
$('recordStop').onclick=()=>postClick($('recordStop'),async()=>{const r=await api('/api/record/stop',{});await loadHistory();return `Сеанс сохранён: ${r.filename}`;});
$('saveHSV').onclick=()=>postClick($('saveHSV'),async()=>{const r=await api('/api/marker',{lower:[Number($('hMin').value),Number($('sMin').value),Number($('vMin').value)],upper:[Number($('hMax').value),255,255]});return 'HSV пороги обновлены: '+r.lower.join(',');});
function draw(points){
 const canvas=$('plot'),ctx=canvas.getContext('2d');const rect=canvas.getBoundingClientRect();const dpr=window.devicePixelRatio||1;
 if(canvas.width!==Math.round(rect.width*dpr)||canvas.height!==Math.round(rect.height*dpr)){canvas.width=Math.round(rect.width*dpr);canvas.height=Math.round(rect.height*dpr);}ctx.setTransform(dpr,0,0,dpr,0,0);
 const w=rect.width,h=rect.height;ctx.clearRect(0,0,w,h);ctx.lineWidth=1;
 const style=getComputedStyle(root),grid=style.getPropertyValue('--grid').trim(),accent=style.getPropertyValue('--accent').trim(),ink=style.getPropertyValue('--ink').trim(),muted=style.getPropertyValue('--muted').trim();
 ctx.strokeStyle=grid;for(let i=1;i<7;i++){const y=h*i/7;ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(w,y);ctx.stroke();}
 ctx.strokeStyle=grid;for(let i=1;i<11;i++){const x=w*i/11;ctx.beginPath();ctx.moveTo(x,0);ctx.lineTo(x,h);ctx.stroke();}
 $('plotEmpty').style.display=points.length?'none':'block';if(!points.length)return;
 const transform=([x,y,z])=>[z-x*.75,-y-x*.27];const coords=points.map(transform);const xx=coords.map(p=>p[0]),yy=coords.map(p=>p[1]);
 const minX=Math.min(...xx),maxX=Math.max(...xx),minY=Math.min(...yy),maxY=Math.max(...yy);
 const span=Math.max(maxX-minX,maxY-minY,.05);const scale=Math.min(w*.82,h*.80)/span;
 const xmid=(minX+maxX)/2,ymid=(minY+maxY)/2;
 ctx.beginPath();coords.forEach((p,i)=>{const x=w/2+(p[0]-xmid)*scale,y=h/2-(p[1]-ymid)*scale;i?ctx.lineTo(x,y):ctx.moveTo(x,y);});ctx.strokeStyle=accent;ctx.lineWidth=2;ctx.stroke();
 const last=coords.at(-1);const lx=w/2+(last[0]-xmid)*scale,ly=h/2-(last[1]-ymid)*scale;
 ctx.beginPath();ctx.arc(lx,ly,4.5,0,Math.PI*2);ctx.fillStyle=ink;ctx.fill();ctx.font='11px sans-serif';ctx.fillStyle=muted;ctx.fillText('Изометрическая проекция / X–Y–Z',18,h-16);
}
for(const link of document.querySelectorAll('.nav a'))link.addEventListener('click',()=>{document.querySelectorAll('.nav a').forEach(item=>item.classList.remove('active'));link.classList.add('active');});
for(const id of ['macPreview','iphonePreview']){const image=$(id);image.onload=()=>image.style.opacity='1';image.onerror=()=>image.style.opacity='0';}
refresh();refreshImages();setInterval(refresh,900);setInterval(refreshImages,400);setInterval(loadHistory,9000);

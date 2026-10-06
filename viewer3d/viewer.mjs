import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {MeshoptDecoder} from 'three/addons/libs/meshopt_decoder.module.js';

const $=id=>document.getElementById(id);
const labels={substrate:'衬底',nwell:'N 阱',diff:'有源区',poly:'多晶硅',licon:'局部接触',li1:'局部互连',
  mcon:'接触',met1:'金属 1',via:'通孔 1',met2:'金属 2',via2:'通孔 2',met3:'金属 3',
  via3:'通孔 3',met4:'金属 4',via4:'通孔 4',met5:'金属 5'};
const metal=n=>/^(met|via|mcon)/.test(n),fill=n=>/^(decap|fill|tapvpwrvgnd)/.test(n);
function fail(e){$('status').hidden=false;$('status').textContent='载入失败：'+(e.message||e);$('status').dataset.error='true';}
async function json(url){const r=await fetch(url);if(!r.ok)throw Error(url+' HTTP '+r.status);return r.json();}

async function main(){
  const data=await json('viewer.json');
  const [x0,y0,x1,y1]=data.bbox_um,W=x1-x0,H=y1-y0,cx=(x0+x1)/2,cy=(y0+y1)/2;
  document.title='吟游诗人 Bard · '+data.design+' · 3D 版图';
  const block={int_c16_q_matrix:'Q 矩阵核',int_c16_prefix_leaf:'前缀状态叶块'}[data.design];
  $('design-name').textContent=block?'吟游诗人 Bard · '+block:data.design;
  $('dimensions').textContent=`SKY130A · ${W.toFixed(3)} × ${H.toFixed(3)} µm`;
  $('size').textContent=`${W.toFixed(3)} × ${H.toFixed(3)} µm`;
  $('area').textContent=(W*H/1e6).toFixed(6)+' mm²';
  $('scope').textContent=data.design==='int_c16_q_matrix'
    ?'真实第 0 层 Q128×128 三值权重、DOT32、整数缩放与激活状态。这是代表硬核，整颗语言模型芯片尚未完成。'
    :data.scope;
  if(data.design==='int_c16_prefix_leaf')$('scope').textContent='64×20 位前缀状态叶块，保持原整数精度及保持/旋转协议。32 份覆盖原前缀银行，游标由外部共用；完整前缀控制和整机版图尚未完成。';
  const m=data.metrics;
  $('signoff').textContent=`DRC / LVS / XOR：${m.magic__drc_error__count} / ${m.design__lvs_error__count} / ${m.design__xor_difference__count}。`
    +`天线违规 net：${m.route__antenna_violation__count??'未报告'}；最差角 slew / cap：${m.design__max_slew_violation__count??'未报告'} / ${m.design__max_cap_violation__count??'未报告'}。`;
  $('tt-link').href=data.tt_viewer;$('gds-link').href=data.design+'.gds';$('oas-link').href=data.design+'.oas';
  $('s-inst').textContent=data.instance_count.toLocaleString();
  let logic=0,ff=0;
  for(const [n,k] of Object.entries(data.cell_counts)){
    if(!fill(n))logic+=k;if(/^df/.test(n))ff+=k;
  }
  $('s-logic').textContent=logic.toLocaleString();$('s-ff').textContent=ff.toLocaleString();
  for(const [n,k] of Object.entries(data.cell_counts).sort((a,b)=>b[1]-a[1]).slice(0,10)){
    for(const text of [n,k.toLocaleString()]){const e=document.createElement('span');e.textContent=text;$('cells').append(e);}
  }
  const canvas=$('c'),view=$('view'),renderer=new THREE.WebGLRenderer({canvas,antialias:true});
  renderer.setPixelRatio(Math.min(devicePixelRatio,2));
  const scene=new THREE.Scene();scene.background=new THREE.Color(0x0d1014);
  const camera=new THREE.PerspectiveCamera(35,1,0.05,Math.max(W,H)*12);camera.up.set(0,0,1);
  const controls=new OrbitControls(camera,canvas);controls.enableDamping=!matchMedia('(prefers-reduced-motion: reduce)').matches;
  scene.add(new THREE.HemisphereLight(0xffffff,0x334455,1.6));
  const sun=new THREE.DirectionalLight(0xffffff,1.6);sun.position.set(cx-W,cy-H,Math.max(W,H)*2);scene.add(sun);
  const root=new THREE.Group();scene.add(root);root.scale.z=8;
  function setView(k){const d=Math.max(W,H)*1.65;
    if(k==='top')camera.position.set(cx,cy-0.01,d*1.1);
    else if(k==='side')camera.position.set(cx,cy-d,d*0.12);
    else camera.position.set(cx-d*.55,cy-d*.7,d*.8);
    controls.target.set(cx,cy,0);controls.update();}
  setView('iso');
  function resize(){const r=view.getBoundingClientRect();renderer.setSize(r.width,r.height,false);camera.aspect=r.width/Math.max(1,r.height);camera.updateProjectionMatrix();}
  new ResizeObserver(resize).observe(view);resize();
  $('zs').oninput=e=>{root.scale.z=+e.target.value;$('zv').textContent=e.target.value+'×';};
  for(const k of ['top','iso','side'])$('v-'+k).onclick=()=>setView(k);

  const loader=new GLTFLoader();loader.setMeshoptDecoder(MeshoptDecoder);
  const loaded=new Map(),wanted=new Set();let queue=Promise.resolve();
  function dispose(g){const geometries=new Set(),materials=new Set();g.traverse(o=>{
    if(o.geometry)geometries.add(o.geometry);
    for(const m of (Array.isArray(o.material)?o.material:[o.material]))if(m)materials.add(m);
  });geometries.forEach(g=>g.dispose());materials.forEach(m=>m.dispose());}
  function setLayer(layer,on){const n=layer.name,cb=$('ly-'+n);cb.checked=on;cb.parentElement.classList.toggle('off',!on);
    if(!on){wanted.delete(n);const g=loaded.get(n);if(g){root.remove(g);dispose(g);loaded.delete(n);}canvas.dataset.loaded=String(loaded.size);return;}
    wanted.add(n);
    queue=queue.then(async()=>{
      if(!wanted.has(n)||loaded.has(n))return;
      $('status').hidden=false;$('status').textContent=`正在载入 ${labels[n]||n} · ${(layer.bytes/1048576).toFixed(1)} MB…`;
      const g=await loader.loadAsync(layer.file);
      if(!wanted.has(n)){dispose(g.scene);$('status').hidden=true;return;}
      g.scene.traverse(o=>{if(o.isMesh)for(const m of (Array.isArray(o.material)?o.material:[o.material])){
        m.side=THREE.DoubleSide;m.metalness=0;m.roughness=.55;
      }});
      root.add(g.scene);loaded.set(n,g.scene);canvas.dataset.loaded=String(loaded.size);
      $('status').hidden=true;
    }).catch(e=>{setLayer(layer,false);fail(e);});
  }
  for(const layer of data.layers){const n=layer.name,row=document.createElement('label');row.className='layer off';row.htmlFor='ly-'+n;
    const cb=document.createElement('input');cb.type='checkbox';cb.id='ly-'+n;cb.onchange=()=>setLayer(layer,cb.checked);
    const label=document.createElement('span');label.textContent=(labels[n]||n)+' '+n;
    const sw=document.createElement('span');sw.className='sw';sw.style.background=new THREE.Color(...layer.color.slice(0,3)).getStyle();
    row.append(cb,label,sw);$('layers').append(row);
  }
  $('b-all').onclick=()=>data.layers.forEach(l=>setLayer(l,true));
  $('b-metal').onclick=()=>data.layers.forEach(l=>setLayer(l,metal(l.name)));
  $('b-dev').onclick=()=>data.layers.forEach(l=>setLayer(l,!metal(l.name)));
  for(const l of data.layers)if(['substrate','met2','met3','met4','met5'].includes(l.name))setLayer(l,true);

  // Query the substrate-plane projection, not every triangle/instance per pointer move.
  const {inst}=await json('cells.json'),grid=new Map(),G=10;
  inst.forEach((c,i)=>{for(let x=Math.floor(c[1]/G);x<=Math.floor(c[3]/G);x++)for(let y=Math.floor(c[2]/G);y<=Math.floor(c[4]/G);y++){
    const k=x+','+y;if(!grid.has(k))grid.set(k,[]);grid.get(k).push(i);
  }});
  function cellAt(x,y){let best=null,area=Infinity,fallback=null;
    for(const i of grid.get(Math.floor(x/G)+','+Math.floor(y/G))||[]){const c=inst[i];
      if(x<c[1]||x>c[3]||y<c[2]||y>c[4])continue;
      if(fill(c[0])){fallback??=c;continue;}
      const a=(c[3]-c[1])*(c[4]-c[2]);if(a<area){area=a;best=c;}
    }return best||fallback;
  }
  const ray=new THREE.Raycaster(),ptr=new THREE.Vector2(),point=new THREE.Vector3(),plane=new THREE.Plane(new THREE.Vector3(0,0,1),0);
  let pending=null;
  canvas.addEventListener('pointermove',e=>{pending=e;});canvas.addEventListener('pointerleave',()=>{pending=null;$('tip').hidden=true;});
  function pick(){if(!pending)return;const e=pending;pending=null;const r=canvas.getBoundingClientRect();
    ptr.set((e.clientX-r.left)/r.width*2-1,-(e.clientY-r.top)/r.height*2+1);ray.setFromCamera(ptr,camera);
    const hit=ray.ray.intersectPlane(plane,point);if(!hit||point.x<x0||point.x>x1||point.y<y0||point.y>y1){$('tip').hidden=true;return;}
    const c=cellAt(point.x,point.y);$('tip').textContent=(c?c[0]+' · ':'')+`x ${point.x.toFixed(2)} y ${point.y.toFixed(2)} µm`;
    $('tip').hidden=false;$('tip').style.left=Math.max(4,Math.min(e.clientX-r.left+14,r.width-245))+'px';$('tip').style.top=(e.clientY-r.top+14)+'px';
  }
  (function frame(){requestAnimationFrame(frame);controls.update();pick();renderer.render(scene,camera);})();
  await queue;canvas.dataset.ready='true';
}
main().catch(fail);

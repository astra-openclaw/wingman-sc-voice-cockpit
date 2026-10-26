/**
 * Dependency-free, read-only Star Citizen defaults extractor (Node.js 22.15+).
 * CryXmlB layout cross-checked against unp4k; independent implementation.
 */
import fs from 'node:fs';
import path from 'node:path';
import zlib from 'node:zlib';
import {fileURLToPath} from 'node:url';

export const EXTRACTOR_VERSION = 2;
const TARGETS = ['data/libs/config/defaultprofile.xml', 'data/localization/english/global.ini'];
const normalized = s => s.replaceAll('\\', '/').toLowerCase();
const integer64 = (b, p) => {
  const n = Number(b.readBigUInt64LE(p));
  if (!Number.isSafeInteger(n)) throw new Error('ZIP64 integer exceeds safe range');
  return n;
};
function readAt(fd, pos, size) {
  const b = Buffer.alloc(size);
  let n = 0;
  while (n < size) {
    const got = fs.readSync(fd, b, n, size - n, pos + n);
    if (!got) throw new Error('Unexpected end of archive');
    n += got;
  }
  return b;
}
export function decodeCryXml(b) {
  if (b.subarray(0, 8).toString('latin1') !== 'CryXmlB\0') throw new Error('Not CryXmlB');
  const le = b.readUInt32LE(8) === b.length;
  const u32 = p => le ? b.readUInt32LE(p) : b.readUInt32BE(p);
  const u16 = p => le ? b.readUInt16LE(p) : b.readUInt16BE(p);
  if (u32(8) !== b.length) throw new Error('Invalid CryXmlB length');
  const no = u32(12), nc = u32(16), ao = u32(20), ac = u32(24);
  const co = u32(28), cc = u32(32), so = u32(36), sl = u32(40);
  for (const [off, len] of [[no,nc*28],[ao,ac*8],[co,cc*4],[so,sl]]) {
    if (off + len > b.length) throw new Error('Invalid CryXmlB table bounds');
  }
  function str(off) {
    const start = so + off, end = b.indexOf(0, start);
    if (off >= sl || end < start || end >= so + sl) throw new Error('Invalid CryXmlB string');
    return b.toString('utf8', start, end);
  }
  const nodes = [];
  for (let i=0;i<nc;i++) {
    const p=no+i*28, attrs={}, children=[];
    const na=u16(p+8), nchild=u16(p+10), ai=u32(p+16), ci=u32(p+20);
    if(ai+na>ac || ci+nchild>cc) throw new Error('Invalid CryXmlB node bounds');
    for(let j=0;j<na;j++) { const q=ao+(ai+j)*8; attrs[str(u32(q))]=str(u32(q+4)); }
    for(let j=0;j<nchild;j++) { const id=u32(co+(ci+j)*4); if(id>=nc)throw new Error('Invalid child');children.push(id); }
    nodes.push({tag:str(u32(p)),attrs,children});
  }
  return {nodes,root:0};
}
export function parseIni(text) {
  const labels = new Map();
  for(const line of text.replace(/^\uFEFF/,'').split(/\r?\n/)) {
    const p=line.indexOf('=');
    if(p<0 || /^\s*[;#[]/.test(line)) continue;
    const key=line.slice(0,p).trim().replace(/,P$/i,'').toLowerCase();
    const val=line.slice(p+1).trim();
    labels.set(key,val);
  }
  return labels;
}
export function indexProfile(tree, labels) {
  const {nodes}=tree;
  const clean = v => String(v||'').replace(/<[^>]+>/g,'').replace(/\\n/g,' ').replace(/\s+/g,' ').trim();
  const label = v => !v ? '' : v.startsWith('@') ? clean(labels.get(v.slice(1).replace(/,P$/i,'').toLowerCase()) || v) : clean(v);
  const actions=[];
  // Defaults can inherit timings from named activation modes.
  const activation = new Map();
  for(const n of nodes) if(n.tag.toLowerCase()==='activationmode' && n.attrs.name) activation.set(n.attrs.name,n.attrs);
  const deviceKey = {keyboard:'keyboard',mouse:'mouse',gamepad:'gamepad',joystick:'joystick'};
  function bind(action,dev,input,extra={}) {
    if(input===undefined || input===null) return;
    for(const value of String(input).split('|')) {
      const token=value.trim();
      if(!token || /^\s*$/.test(token.replace(/^(kb|mo|gp|js)\d+_/i,'')))continue;
      const mode=extra.activationMode || action.activationMode || '';
      const data={input:token,activationMode:mode};
      for(const [k,v] of Object.entries({...activation.get(mode),...extra})) {
        if(['activationMode','onPress','onHold','onRelease','multiTap','multiTapBlock','pressTriggerThreshold','releaseTriggerThreshold','releaseTriggerDelay','holdTriggerDelay','retriggerable','activationMap'].includes(k)) data[k]=v;
      }
      if(!action[dev].some(b=>JSON.stringify(b)===JSON.stringify(data))) action[dev].push(data);
    }
  }
  for(const m of nodes) if(m.tag.toLowerCase()==='actionmap') {
    for(const id of m.children) {
      const n=nodes[id];if(n.tag.toLowerCase()!=='action')continue;
      const a={map:m.attrs.name||'',mapLabel:label(m.attrs.UILabel||m.attrs.uiLabel||m.attrs.label)||m.attrs.name||'',
        name:n.attrs.name||'',label:label(n.attrs.UILabel||n.attrs.uiLabel||n.attrs.label)||n.attrs.name||'',
        description:label(n.attrs.UIDescription||n.attrs.uiDescription||n.attrs.description),
        activationMode:n.attrs.activationMode||'',keyboard:[],mouse:[],gamepad:[],joystick:[]};
      for(const dev of Object.keys(deviceKey)) bind(a,dev,n.attrs[dev],n.attrs);
      function childInputs(id,device='',inherited=n.attrs,depth=0) {
        if(depth>16)throw new Error('Device nesting too deep');
        const c=nodes[id],tag=c.tag.toLowerCase(),e={...inherited,...c.attrs};
        const d=deviceKey[tag]||device;
        if(c.attrs.input!==undefined) {
          const t=String(c.attrs.type||c.attrs.device||'').toLowerCase(),s=c.attrs.input;
          const slot=d||deviceKey[t]||(/^mo\d+_/i.test(s)?'mouse':/^gp\d+_/i.test(s)?'gamepad':/^js\d+_/i.test(s)?'joystick':'keyboard');
          bind(a,slot,s,e);
        }
        for(const dev of Object.keys(deviceKey)) bind(a,dev,c.attrs[dev],e);
        for(const cid of c.children) childInputs(cid,d,e,depth+1);
      }
      for(const cid of n.children) childInputs(cid);
      actions.push(a);
    }
  }
  return actions;
}
function findEntries(fd,size) {
  const tlen=Math.min(size,65557+20), tail=readAt(fd,size-tlen,tlen);
  let ep=-1;
  for(let p=tail.length-22;p>=0;p--) if(tail.readUInt32LE(p)===0x06054b50){ep=p;break;}
  if(ep<0) throw new Error('ZIP end record missing');
  let off=tail.readUInt32LE(ep+16),len=tail.readUInt32LE(ep+12);
  if(ep>=20 && tail.readUInt32LE(ep-20)===0x07064b50) {
    const zip=readAt(fd,integer64(tail,ep-12),56);
    if(zip.readUInt32LE(0)!==0x06064b50)throw new Error('ZIP64 record missing');
    off=integer64(zip,48);len=integer64(zip,40);
  }
  if(off+len>size)throw new Error('Invalid central directory bounds');
  let pos=off, base=-1, block=Buffer.alloc(0);
  function window(n) {
    if(pos<base || pos+n>base+block.length) {base=pos;block=readAt(fd,pos,Math.min(4*1024*1024,off+len-pos));}
    if(pos+n>base+block.length)throw new Error('Truncated directory entry');
    return pos-base;
  }
  const entries={};
  while(pos<off+len) {
    let p=window(46);
    if(block.readUInt32LE(p)!==0x02014b50)throw new Error('Invalid directory entry');
    const nl=block.readUInt16LE(p+28),xl=block.readUInt16LE(p+30),cl=block.readUInt16LE(p+32),n=46+nl+xl+cl;
    p=window(n);
    const name=normalized(block.toString('utf8',p+46,p+46+nl));
    if(TARGETS.includes(name)) {
      const f={name,flags:block.readUInt16LE(p+8),method:block.readUInt16LE(p+10),
        comp:block.readUInt32LE(p+20),uncomp:block.readUInt32LE(p+24),lho:block.readUInt32LE(p+42)};
      let q=p+46+nl;
      while(q+4<=p+46+nl+xl) {
        const type=block.readUInt16LE(q),l=block.readUInt16LE(q+2);
        // Star Citizen appends a proprietary extra record; only ZIP64 is standard.
        if(type===1) {
          if(q+4+l>p+46+nl+xl)throw new Error('Truncated ZIP64 extra field');
          let z=q+4;
          for(const key of ['uncomp','comp','lho']) if(f[key]===0xffffffff){if(z+8>q+4+l)throw new Error('Truncated ZIP64');f[key]=integer64(block,z);z+=8;}
        }
        q+=4+l;
      }
      entries[name]=f;
      if(Object.keys(entries).length===TARGETS.length)break;
    }
    pos+=n;
  }
  if(Object.keys(entries).length!==TARGETS.length)throw new Error('Required game resources missing');
  return entries;
}
function decodeEntry(fd,f,size) {
  if(f.flags&1)throw new Error('Encrypted resource is unsupported');
  const lh=readAt(fd,f.lho,30);
  if(lh.readUInt32LE(0)!==0x04034b50)throw new Error('Invalid local header');
  const off=f.lho+30+lh.readUInt16LE(26)+lh.readUInt16LE(28);
  if(f.comp>32*1024*1024 || f.uncomp>64*1024*1024 || off+f.comp>size)throw new Error('Resource bounds exceeded');
  const raw=readAt(fd,off,f.comp);
  const b=f.method===100?zlib.zstdDecompressSync(raw):f.method===8?zlib.inflateRawSync(raw):f.method===0?raw:null;
  if(!b || b.length!==f.uncomp)throw new Error('Unsupported method or resource length mismatch');
  return b;
}
function atomicJSON(file,data) {
  fs.mkdirSync(path.dirname(file),{recursive:true});
  const tmp=file+'.'+process.pid+'.tmp';
  try {fs.writeFileSync(tmp,JSON.stringify(data));fs.renameSync(tmp,file);}
  finally {if(fs.existsSync(tmp))fs.unlinkSync(tmp);}
}
export function extract(game,out) {
  const started=performance.now();
  const manifest=JSON.parse(fs.readFileSync(path.join(game,'build_manifest.id'),'utf8').replace(/^\uFEFF/,''));
  const build=manifest.Data||manifest;
  const fd=fs.openSync(path.join(game,'Data.p4k'),'r');
  try {
    const st=fs.fstatSync(fd),key={p4kSize:st.size,p4kMtime:st.mtime.toISOString()};
    try {
      const old=JSON.parse(fs.readFileSync(out,'utf8'));
      if(old.metadata.buildId===build.BuildId && old.metadata.extractorVersion===EXTRACTOR_VERSION &&
         old.metadata.p4kSize===key.p4kSize && old.metadata.p4kMtime===key.p4kMtime && old.actions?.length) {
        return {noop:true,seconds:(performance.now()-started)/1000,actions:old.actions.length,bytes:fs.statSync(out).size,metadata:old.metadata};
      }
    }catch{}
    let entries;
    try{const c=JSON.parse(fs.readFileSync(out+'.entries.json','utf8'));if(c.p4kSize===key.p4kSize&&c.p4kMtime===key.p4kMtime&&TARGETS.every(k=>c.entries[k]))entries=c.entries;}catch{}
    if(!entries){entries=findEntries(fd,st.size);atomicJSON(out+'.entries.json',{...key,entries});}
    const tree=decodeCryXml(decodeEntry(fd,entries[TARGETS[0]],st.size));
    const labels=parseIni(decodeEntry(fd,entries[TARGETS[1]],st.size).toString('utf8'));
    const actions=indexProfile(tree,labels);
    if(!actions.length)throw new Error('No actions decoded');
    const metadata={buildId:build.BuildId,version:build.Version,branch:build.Branch,...key,
      extractedAt:new Date().toISOString(),extractorVersion:EXTRACTOR_VERSION};
    atomicJSON(out,{metadata,actions});
    return {noop:false,seconds:(performance.now()-started)/1000,actions:actions.length,bytes:fs.statSync(out).size,metadata};
  } finally {fs.closeSync(fd);}
}
if(process.argv[1] && path.resolve(process.argv[1])===fileURLToPath(import.meta.url)) {
  try {
    const [major,minor]=process.versions.node.split('.').map(Number);
    if(major<22 || (major===22 && minor<15) || typeof zlib.zstdDecompressSync!=='function')throw new Error('Node.js 22.15+ with zstd is required');
    const args=process.argv.slice(2);
    if(args.length!==4 || args[0]!=='--game'||args[2]!=='--out')throw new Error('Usage: node extract_defaults.mjs --game <LIVE> --out <json>');
    console.log(JSON.stringify(extract(args[1],args[3])));
  }catch(error){console.error('SC keybindings: '+error.message);process.exitCode=1;}
}

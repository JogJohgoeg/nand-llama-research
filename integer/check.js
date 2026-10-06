const fs=require('node:fs'),vm=require('node:vm'),crypto=require('node:crypto');
const html=fs.readFileSync('docs/index.html','utf8');
const data=JSON.parse(html.match(/<script id="payload" type="application\/json">([\s\S]*?)<\/script>/)[1]);
vm.runInThisContext(html.match(/<script id="engine">([\s\S]*?)<\/script>/)[1]);
const sha=b=>crypto.createHash('sha256').update(b).digest('hex');
const le=a=>{const b=Buffer.alloc(a.length*4);a.forEach((v,i)=>b.writeInt32LE(v,i*4));return b;};
const bytes=Uint8Array.from(Buffer.from(data.model,'base64'));
if(sha(bytes)!==data.sha256)throw Error('Model SHA');
const model=IntC16.load(bytes.buffer),cases=[];
for(const f of data.fixtures){const a=model.forward(f.ids),logits=sha(le(a.logits)),trace=sha(le(a.trace));if(logits!==f.logit_sha256||trace!==f.trace_sha256)throw Error(f.name);cases.push({name:f.name,logits,trace});}
const begin=html.indexOf('<script id="engine">'),end=html.indexOf('</script>',begin);
const mutated=html.slice(begin+'<script id="engine">'.length,end).replace('2*rem===d && q%2===1','2*rem===d');
vm.runInThisContext(mutated);const bad=IntC16.load(bytes.buffer),f=data.fixtures[3];
if(sha(le(bad.forward(f.ids).logits))===f.logit_sha256)throw Error('Rounding mutation not caught');
fs.mkdirSync('build',{recursive:true});fs.writeFileSync('build/integer_js.json',JSON.stringify({status:'pass',model_sha256:data.sha256,cases,actual_rounding_mutation_rejected:true},null,2)+'\n');
console.log('Browser integer engine',cases.length,'C golden cases and rounding mutation PASS');

const enc = new TextEncoder(), dec = new TextDecoder();
const b64 = b => {let a=new Uint8Array(b),s='';for(let i=0;i<a.length;i+=8192)s+=String.fromCharCode(...a.subarray(i,i+8192));return btoa(s).replaceAll('+','-').replaceAll('/','_')};
const un64 = s => Uint8Array.from(atob(s.replaceAll('-','+').replaceAll('_','/')), c=>c.charCodeAt(0));
export async function encrypt(value,key,purpose) {const nonce=crypto.getRandomValues(new Uint8Array(12)); const k=await crypto.subtle.importKey('raw',un64(key),'AES-GCM',false,['encrypt']);const data=await crypto.subtle.encrypt({name:'AES-GCM',iv:nonce,additionalData:enc.encode('teleping:v1:'+purpose)},k,enc.encode(JSON.stringify(value)));return JSON.stringify({v:1,nonce:b64(nonce),ciphertext:b64(data)});}
export async function decrypt(value,key,purpose) {const e=JSON.parse(value);if(e.v!==1)throw Error('invalid');const k=await crypto.subtle.importKey('raw',un64(key),'AES-GCM',false,['decrypt']);return JSON.parse(dec.decode(await crypto.subtle.decrypt({name:'AES-GCM',iv:un64(e.nonce),additionalData:enc.encode('teleping:v1:'+purpose)},k,un64(e.ciphertext))));}
export function normalize(value){
 if(typeof value!=='string'||!value.trim()||/[\s\x00-\x1f\x7f#]/.test(value.trim()))throw Error('invalid');
 let u=new URL(value.includes('://')?value.trim():'http://'+value.trim());
 if(!['http:','https:'].includes(u.protocol)||u.username||u.password)throw Error('invalid');
 let host=u.hostname.toLowerCase().replace(/\.$/,'');
 if(host.startsWith('[')){
  let raw=host.slice(1,-1), first=parseInt(raw.split(':')[0]||'0',16);
  if(first<0x2000||first>0x3fff||raw.startsWith('2001:db8:')||raw==='2001:db8::')throw Error('invalid');
 }else if(/^\d+(\.\d+){3}$/.test(host)){
  let [a,b,c]=host.split('.').map(Number);
  if(a===0||a===10||a===127||a>=224||(a===100&&b>=64&&b<=127)||(a===169&&b===254)||(a===172&&b>=16&&b<=31)||(a===192&&(b===168||b===0||b===2))||(a===198&&(b===18||b===19||b===51&&c===100))||(a===203&&b===0&&c===113))throw Error('invalid');
 }else if(!host.includes('.')||/\.(localhost|local|internal|lan)$/.test(host)||host.split('.').some(x=>! /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(x)))throw Error('invalid');
 u.hostname=host;return u.toString();
}
export function chunks(s){let a=[];while(s.length>3500){let n=3500;if(/[\uD800-\uDBFF]/.test(s[n-1]))n--;a.push(s.slice(0,n));s=s.slice(n)}if(s)a.push(s);return a}

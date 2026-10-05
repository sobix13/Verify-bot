/* No third-party scripts, cookies or persistent wallet storage. */
'use strict';
const token=window.location.hash.slice(1);
const statusNode=document.getElementById('status');
const button=document.getElementById('connect');
async function api(path,body){const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),credentials:'omit'});const data=await response.json();if(!response.ok)throw new Error(data.error||'Connection failed. Create a new link in Discord.');return data;}
button.addEventListener('click',async()=>{
 button.disabled=true;
 try{
  if(!token)throw new Error('Open the private connection link from Discord.');
  const chosen=document.getElementById('provider').value;
  const wallet=chosen==='phantom'?window.phantom?.solana:chosen==='solflare'?window.solflare:window.solana;
  if(!wallet?.connect||!wallet?.signMessage)throw new Error('Open this page in a compatible Solana wallet browser or enable its extension.');
  statusNode.textContent='Connecting to your wallet…';
  const connection=await wallet.connect();
  const publicKey=(connection?.publicKey||wallet.publicKey)?.toString();
  if(!publicKey)throw new Error('The wallet did not provide a public address.');
  const challenge=await api('/api/challenge',{token,wallet:publicKey});
  statusNode.textContent='Read and sign the connection message in your wallet.';
  const signed=await wallet.signMessage(new TextEncoder().encode(challenge.message),'utf8');
  const bytes=signed.signature||signed;
  const signature=btoa(Array.from(bytes,b=>String.fromCharCode(b)).join(''));
  await api('/api/verify',{token,wallet:publicKey,signature});
  statusNode.textContent='Wallet connected. Return to Discord and open My status.';
  history.replaceState(null,'',window.location.pathname);
 }catch(error){statusNode.textContent=String(error.message||'Connection failed.');button.disabled=false;}
});

const {chromium}=require('playwright');
(async()=>{const b=await chromium.launch();const p=await b.newPage({viewport:{width:1080,height:800},deviceScaleFactor:+process.argv[3]||1});
await p.goto('file://'+__dirname+'/s2.html');await p.waitForTimeout(500);
await p.screenshot({path:process.argv[2],fullPage:true});await b.close()})();

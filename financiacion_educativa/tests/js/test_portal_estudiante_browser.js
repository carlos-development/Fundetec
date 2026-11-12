'use strict';
const assert=require('node:assert/strict');
const path=require('node:path');
const test=require('node:test');

test('Portal propio: navegacion, cuotas e historial sin JavaScript',async t=>{
    const {chromium}=require(process.env.EDU_PLAYWRIGHT_MODULE);
    const browser=await chromium.launch({headless:true,channel:'msedge'});
    t.after(()=>browser.close());
    const base=process.env.EDU_PORTAL_BASE;
    const context=await browser.newContext({javaScriptEnabled:false,locale:'es-CO',reducedMotion:'reduce'});
    await context.route('**/*',r=>new URL(r.request().url()).origin===base?r.continue():r.abort());
    await context.addCookies([{name:'sessionid',value:process.env.EDU_PORTAL_SESSION,url:base}]);
    const page=await context.newPage();
    for(const width of [375,390,1440]){
        await page.setViewportSize({width,height:width>700?1000:844});
        assert.equal((await page.goto(base+process.env.EDU_PORTAL_LIST)).status(),200);
        await page.getByRole('heading',{name:'Mis solicitudes',exact:true}).waitFor();
        assert.equal(await page.locator('.edu-student-tabs a').count(),2);
        await page.screenshot({path:path.join(process.env.EDU_PORTAL_SCREENSHOTS,`tramites-${width}.png`),fullPage:true});
        await page.locator('.edu-student-tabs a').filter({hasText:'Financiaciones aprobadas'}).click();
        await page.getByRole('link',{name:'Ver financiación',exact:true}).waitFor();
        await page.screenshot({path:path.join(process.env.EDU_PORTAL_SCREENSHOTS,`financiaciones-${width}.png`),fullPage:true});
        await page.getByRole('link',{name:'Ver financiación',exact:true}).click();
        await page.locator('#history-title').waitFor();
        assert.equal(await page.locator('.edu-student-metrics').innerText().then(x=>x.includes('Cuota 2')),true);
        const rows=await page.locator('.edu-student-section tbody').innerText();
        assert.match(rows,/Pago aplicado/);assert.match(rows,/Transferencia confirmada/);
        assert.equal((await page.locator('main').innerText()).includes(process.env.EDU_PORTAL_OPERATOR),false);
        assert.equal((await page.locator('main').innerText()).includes(process.env.EDU_PORTAL_BANK_REFERENCE),false);
        assert.equal(await page.locator('main a[href*="banco="]').count(),0);
        assert.equal(await page.getByRole('link',{name:'Pagar y reportar transferencia',exact:true}).count(),0);
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
        assert.equal(await page.locator('.edu-navbar-logo img').evaluate(img=>img.complete&&img.naturalWidth>0),true);
        await page.screenshot({path:path.join(process.env.EDU_PORTAL_SCREENSHOTS,`financiacion-${width}.png`),fullPage:true});
        await page.getByText('Calendario y aplicaciones',{exact:true}).click();
        assert.equal(await page.locator('.edu-student-details[open] table').isVisible(),true);
        await page.screenshot({path:path.join(process.env.EDU_PORTAL_SCREENSHOTS,`calendario-${width}.png`),fullPage:true});
    }
    assert.equal((await context.request.get(base+process.env.EDU_PORTAL_BANK)).status(),403);
    const other=await browser.newContext({javaScriptEnabled:false});
    await other.addCookies([{name:'sessionid',value:process.env.EDU_PORTAL_OTHER_SESSION,url:base}]);
    assert.equal((await other.request.get(base+process.env.EDU_PORTAL_FINANCE)).status(),404);
});

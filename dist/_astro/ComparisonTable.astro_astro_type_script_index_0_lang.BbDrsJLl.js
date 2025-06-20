const Y="modulepreload",X=function(E){return"/"+E},U={},Q=function(h,$,_){let L=Promise.resolve();if($&&$.length>0){let v=function(p){return Promise.all(p.map(w=>Promise.resolve(w).then(f=>({status:"fulfilled",value:f}),f=>({status:"rejected",reason:f}))))};document.getElementsByTagName("link");const y=document.querySelector("meta[property=csp-nonce]"),P=y?.nonce||y?.getAttribute("nonce");L=v($.map(p=>{if(p=X(p),p in U)return;U[p]=!0;const w=p.endsWith(".css"),f=w?'[rel="stylesheet"]':"";if(document.querySelector(`link[href="${p}"]${f}`))return;const g=document.createElement("link");if(g.rel=w?"stylesheet":Y,w||(g.as="script"),g.crossOrigin="",g.href=p,P&&g.setAttribute("nonce",P),document.head.appendChild(g),w)return new Promise((S,T)=>{g.addEventListener("load",S),g.addEventListener("error",()=>T(new Error(`Unable to preload CSS for ${p}`)))})}))}function m(v){const y=new Event("vite:preloadError",{cancelable:!0});if(y.payload=v,window.dispatchEvent(y),!y.defaultPrevented)throw v}return L.then(v=>{for(const y of v||[])y.status==="rejected"&&m(y.reason);return h().catch(m)})};document.addEventListener("DOMContentLoaded",()=>{const E=document.getElementById("table-body"),h=document.getElementById("result-count"),$=document.getElementById("column-controls-btn"),_=document.getElementById("column-controls");let L=Array.from(document.querySelectorAll(".instance-row")).map(e=>JSON.parse(e.dataset.instance||"{}")),m="ipv4_ipv6",v="",y="asc",P={},p=localStorage.getItem("selectedCurrency")||"USD",w={USD:1},f="",g=!1,S=null;window.addEventListener("currencyChanged",e=>{const o=e,{currency:t,symbol:s,rates:r}=o.detail;p=t,w=r,document.querySelectorAll(".pricing-value[data-usd-price]").forEach(i=>{const n=parseFloat(i.getAttribute("data-usd-price")||"0");if(n>0){const c=r[t]||1,l=n*c;let a;if(["SEK","NOK","DKK"].includes(t))a=`${l.toFixed(2)} ${s}`;else if(t==="JPY")a=`${s}${Math.round(l)}`;else{const d=i.closest(".pricing-hourly")?4:2;a=`${s}${l.toFixed(d)}`}i.textContent=a}})});function T(e,o=!1){if(!e||e===0)return"-";const s={USD:"$",EUR:"€",GBP:"£",SEK:"kr",NOK:"kr",DKK:"kr",CHF:"Fr",CAD:"$",AUD:"$",JPY:"¥"}[p]||p,r=w[p]||1,i=e*r;if(["SEK","NOK","DKK"].includes(p)){const n=o?4:2;return`${i.toFixed(n)} ${s}`}else{if(p==="JPY")return`${s}${Math.round(i)}`;{const n=o?4:2;return`${s}${i.toFixed(n)}`}}}function b(e,o=!1){if(!e||e===0)return"-";const t=w.EUR||.92,s=e/t;return T(s,o)}const A=document.getElementById("search-input"),k=document.getElementById("clear-search");A&&(A.addEventListener("input",e=>{f=e.target.value.toLowerCase(),k&&(f?k.classList.remove("hidden"):k.classList.add("hidden")),N()}),k&&k.addEventListener("click",()=>{A.value="",f="",k.classList.add("hidden"),N()})),$&&_&&($.addEventListener("click",()=>{_.classList.toggle("hidden")}),document.addEventListener("click",e=>{!$.contains(e.target)&&!_.contains(e.target)&&_.classList.add("hidden")})),document.querySelectorAll('#column-controls input[type="checkbox"]').forEach(e=>{e.addEventListener("change",o=>{const t=o.target,s=t.id.replace("col-","col-"),r=t.checked;document.querySelectorAll(`.${s}`).forEach(n=>{r?n.classList.remove("hidden"):n.classList.add("hidden")})})}),document.querySelectorAll("th[data-sort]").forEach(e=>{e.addEventListener("click",()=>{const o=e.getAttribute("data-sort");o&&(v===o?y=y==="asc"?"desc":"asc":y="asc",v=o,B(o,y),F())})});function D(){document.querySelectorAll(".regions-more-trigger").forEach(e=>{const o=e.querySelector(".all-regions-popup");if(!o)return;let t,s;e.addEventListener("mouseenter",()=>{clearTimeout(s),t=setTimeout(()=>{o.classList.remove("invisible")},200)}),e.addEventListener("mouseleave",()=>{clearTimeout(t),s=setTimeout(()=>{o.classList.add("invisible")},300)}),o.addEventListener("mouseenter",()=>{clearTimeout(s)}),o.addEventListener("mouseleave",()=>{s=setTimeout(()=>{o.classList.add("invisible")},300)})})}D(),document.addEventListener("filtersChanged",e=>{const t=e.detail;P=t,t.networkOptions?.length===1?m=t.networkOptions[0]:m="ipv4_ipv6",I(t),t.regions?.length&&M(t.regions)});function F(){if(document.querySelectorAll(".sort-icon").forEach(o=>{o.innerHTML='<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 16V4m0 0L3 8m4-4l4 4m6 0v12m0 0l4-4m-4 4l-4-4"></path>',o.classList.remove("text-primary-600"),o.classList.add("text-gray-400")}),v){const o=document.querySelector(`th[data-sort="${v}"] .sort-icon`);o&&(o.classList.remove("text-gray-400"),o.classList.add("text-primary-600"),y==="asc"?o.innerHTML='<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 4l6 6 6-6"></path>':o.innerHTML='<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 16l-6-6-6 6"></path>')}}function B(e,o){const s=Array.from(document.querySelectorAll(".instance-row:not(.hidden)")).map(r=>JSON.parse(r.dataset.instance||"{}"));s.sort((r,i)=>{let n=r[e],c=i[e];if(e.includes(".")){const d=e.split(".");n=d.reduce((x,C)=>x?.[C],r),c=d.reduce((x,C)=>x?.[C],i)}if(n==null&&c==null)return 0;if(n==null)return o==="asc"?1:-1;if(c==null)return o==="asc"?-1:1;if(typeof n=="string"&&typeof c=="string"){const d=n.localeCompare(c);return o==="asc"?d:-d}const l=Number(n),a=Number(c);if(!isNaN(l)&&!isNaN(a))return o==="asc"?l-a:a-l;const u=String(n).localeCompare(String(c));return o==="asc"?u:-u}),q(s)}function M(e){document.querySelectorAll(".regions-container").forEach(t=>{const s=JSON.parse(t.getAttribute("data-regions")||"[]");if(!s.length)return;const r=[...s].sort((i,n)=>{const c=e.some(a=>a===i.country||a===i.code||a===i.region),l=e.some(a=>a===n.country||a===n.code||a===n.region);return c&&!l?-1:!c&&l?1:0});R(t,r)}),D()}function R(e,o){e.innerHTML="";const s=o.slice(0,3),r=Math.max(0,o.length-3);if(s.forEach(i=>{const n=document.createElement("div");n.className="relative group",n.innerHTML=`
          <span class="inline-flex items-center space-x-1 cursor-pointer hover:bg-gray-100 px-1 py-0.5 rounded">
            <img 
              src="https://flagsapi.com/${i.countryCode}/flat/16.png"
              alt="${i.country}"
              class="w-4 h-3 rounded-sm"
              loading="lazy"
            />
            <span class="text-xs">${i.code}</span>
          </span>
          <div class="absolute z-20 invisible group-hover:visible bg-gray-900 text-white text-xs px-2 py-1 rounded shadow-lg bottom-full left-1/2 transform -translate-x-1/2 mb-1 whitespace-nowrap">
            <div class="font-medium">${i.city}, ${i.country}</div>
            <div class="text-gray-300">${i.region}</div>
            <div class="absolute top-full left-1/2 transform -translate-x-1/2 w-0 h-0 border-l-4 border-r-4 border-t-4 border-transparent border-t-gray-900"></div>
          </div>
        `,e.appendChild(n)}),r>0){const i=document.createElement("div");i.className="relative group cursor-pointer";const n=o.map(c=>`
          <div class="flex items-center space-x-2 py-1">
            <img 
              src="https://flagsapi.com/${c.countryCode}/flat/16.png"
              alt="${c.country}"
              class="w-4 h-3 rounded-sm flex-shrink-0"
              loading="lazy"
            />
            <div class="flex-1 min-w-0">
              <div class="text-xs font-medium text-gray-900 truncate">${c.city}, ${c.country}</div>
              <div class="text-xs text-gray-500 truncate">${c.code} • ${c.region}</div>
            </div>
          </div>
        `).join("");i.innerHTML=`
          <span class="text-xs text-gray-400 hover:text-gray-600">+${r}</span>
          <div class="all-regions-popup absolute z-30 invisible bg-white border border-gray-200 rounded-lg shadow-lg p-3 bottom-full right-0 mb-2 w-64">
            <div class="text-xs font-medium text-gray-900 mb-2">All Available Regions:</div>
            <div class="grid grid-cols-1 gap-1 max-h-40 overflow-y-auto">
              ${n}
            </div>
            <div class="absolute top-full right-4 w-0 h-0 border-l-4 border-r-4 border-t-4 border-transparent border-t-gray-200"></div>
          </div>
        `,i.className="relative regions-more-trigger cursor-pointer",e.appendChild(i)}}function I(e){const o=L.filter(t=>{if(e.providers?.length&&!e.providers.includes(t.provider))return!1;if(e.regions?.length){let r=!1;if(t.regionalPricing&&Array.isArray(t.regionalPricing)){const i=[];t.locationDetails&&Array.isArray(t.locationDetails)&&i.push(...t.locationDetails.filter(n=>e.regions.some(c=>c===n.country||c===n.code||c===n.region||c===n.city||c.toLowerCase()===n.country.toLowerCase()||c.toLowerCase()===n.code.toLowerCase()))),r=i.length>0&&i.some(n=>t.regionalPricing.some(c=>c.location===n.code))}else t.locationDetails&&Array.isArray(t.locationDetails)?r=t.locationDetails.some(i=>e.regions.some(n=>n===i.country||n===i.code||n===i.region||n===i.city||n.toLowerCase()===i.country.toLowerCase()||n.toLowerCase()===i.code.toLowerCase())):t.regions&&Array.isArray(t.regions)&&(r=t.regions.some(i=>e.regions.some(n=>n===i||n.toLowerCase()===i.toLowerCase())));if(!r)return!1}if(e.instanceTypes?.length&&!e.instanceTypes.includes(t.type))return!1;if(e.ipTypes?.length){const r=t.networkType||t.ipType;if(r&&!e.ipTypes.includes(r))return!1}if(e.networkOptions?.length){const r=t.networkType||t.networkOptions;if(r&&typeof r=="object"){if(!e.networkOptions.some(n=>r[n]&&r[n].available))return!1}else if(r&&typeof r=="string"&&!e.networkOptions.includes(r))return!1}if(t.vCPU<e.minVCPU||t.vCPU>e.maxVCPU||t.memoryGiB<e.minMemory||t.memoryGiB>e.maxMemory)return!1;let s=0;if(e.regions?.length===1&&t.regionalPricing&&t.locationDetails){const r=e.regions[0],i=t.locationDetails.find(n=>r===n.country||r===n.code||r.toLowerCase()===n.country.toLowerCase()||r.toLowerCase()===n.code.toLowerCase());if(i){const n=t.regionalPricing.find(c=>c.location===i.code);n&&n.hourly_net&&(s=n.hourly_net)}}if(s===0){if(t.networkOptions&&typeof t.networkOptions=="object"){const r=t.networkOptions[m];r&&r.available&&r.hourly!==null&&(s=r.hourly)}s===0&&(s=t.priceUSD_hourly||t.priceEUR_hourly_net||0)}return t.type==="cloud-server"&&t.instanceType==="cx22"&&console.log("  Final price to check:",s,"vs max price:",e.maxPrice),s>e.maxPrice?(t.type==="cloud-server"&&t.instanceType==="cx22"&&console.log("  FAILED price filter. Price:",s,"Max:",e.maxPrice),!1):(t.type==="cloud-server"&&t.instanceType==="cx22"&&console.log("  PASSED ALL FILTERS! 🎉"),!0)});console.log(`Filtered result: ${o.length} instances`),console.log("Filtered examples:",o.slice(0,5).map(t=>({instanceType:t.instanceType,type:t.type}))),q(o),f!==""&&N(),e.regions?.length===1?z(e.regions[0]):H(),f===""&&h&&(h.textContent=`${o.length} instances`)}function z(e){document.querySelectorAll(".instance-row:not(.hidden)").forEach(o=>{const t=JSON.parse(o.dataset.instance||"{}");if(!t.locationDetails||!t.regionalPricing)return;const s=t.locationDetails.find(r=>e===r.country||e===r.code||e.toLowerCase()===r.country.toLowerCase()||e.toLowerCase()===r.code.toLowerCase());if(s){const r=t.regionalPricing.find(i=>i.location===s.code);if(r){const i=t.hetzner_metadata?.ipv4_primary_ip_cost||.5;let n=r.hourly_net,c=r.monthly_net;m==="ipv4_ipv6"&&(n+=i/730.44,c+=i);const l=o.querySelector(".pricing-hourly .pricing-value");l&&(l.textContent=b(n,!0));const a=o.querySelector(".pricing-monthly .pricing-value");a&&(a.textContent=b(c,!1));const u=o.querySelector('[data-network-info="true"]');if(u){const d=u.querySelector(".pricing-description");if(d){const x=m==="ipv4_ipv6"?"pricing (incl. IPv4)":"pricing";d.textContent=`${s.city} ${x}`,d.className="text-xs text-blue-600 pricing-description"}}if(r.included_traffic||r.traffic_price_per_tb){const d=document.createElement("div");d.className="text-xs text-gray-500 mt-1";let x="";if(r.included_traffic){const C=(r.included_traffic/1099511627776).toFixed(1);x+=`${C}TB included`}if(r.traffic_price_per_tb&&(x&&(x+=", "),x+=`€${r.traffic_price_per_tb}/TB overage`),x){d.textContent=x;const C=o.querySelector(".pricing-hourly");C&&!C.querySelector(".traffic-info")&&(d.classList.add("traffic-info"),C.appendChild(d))}}}}})}function H(){document.querySelectorAll(".instance-row:not(.hidden)").forEach(e=>{const o=JSON.parse(e.dataset.instance||"{}");e.querySelectorAll(".traffic-info").forEach(n=>n.remove());const s=e.querySelector(".pricing-hourly .pricing-value");if(s){const n=o.priceEUR_hourly_net,c=o.priceUSD_hourly;n?s.textContent=b(n,!0):c?s.textContent=T(c,!0):s.textContent="-"}const r=e.querySelector(".pricing-monthly .pricing-value");if(r){const n=o.priceEUR_monthly_net,c=o.priceUSD_monthly;n?r.textContent=b(n,!1):c?r.textContent=T(c,!1):r.textContent="-"}const i=e.querySelector('[data-network-info="true"]');if(i){const n=i.querySelector(".pricing-description");n&&(n.textContent="Standard pricing",n.className="text-xs text-gray-500 pricing-description")}})}function J(){document.querySelectorAll('.instance-row:not([style*="display: none"])').forEach(e=>{const o=JSON.parse(e.dataset.instance||"{}");e.querySelector('[data-network-info="true"]');const t=e.querySelector(".pricing-mode-indicator"),s=e.querySelector(".pricing-description"),r=e.querySelector(".pricing-hourly"),i=e.querySelector(".pricing-monthly");if(o.networkOptions&&typeof o.networkOptions=="object"){const n=o.networkOptions[m];if(n&&n.available){if(t&&(t.textContent=m==="ipv6_only"?"IPv6-only":"IPv4 + IPv6",t.className=`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium pricing-mode-indicator ${m==="ipv6_only"?"bg-green-100 text-green-800":"bg-cyan-100 text-cyan-800"}`),s&&(m==="ipv6_only"&&n.savings?(s.textContent=`Saves €${n.savings.toFixed(2)}/month`,s.className="text-xs text-green-600 pricing-description"):(s.textContent=n.description||"Standard pricing",s.className="text-xs text-gray-500 pricing-description")),r&&n.hourly!==null){const c=r.querySelector(".pricing-value"),l=r.querySelector(".text-xs.text-orange-600");if(c){const a=n.priceRange?.hourly;if(a&&a.hasVariation){const u=b(a.min,!0),d=b(a.max,!0);c.textContent=`${u} - ${d}`,l&&(l.style.display="")}else c.textContent=b(n.hourly,!0),l&&(l.style.display="none")}}if(i&&n.monthly!==null){const c=i.querySelector(".pricing-value"),l=i.querySelector(".text-xs.text-orange-600");if(c){const a=n.priceRange?.monthly;if(a&&a.hasVariation){const u=b(a.min,!1),d=b(a.max,!1);c.textContent=`${u} - ${d}`,l&&(l.style.display="")}else c.textContent=b(n.monthly,!1),l&&(l.style.display="none")}}e.style.display=""}else m!=="all"&&m!=="ipv4_ipv6"&&(e.style.display="none")}})}function q(e){if(!E)return;const o=Array.from(document.querySelectorAll(".instance-row"));e.some(s=>!o.find(r=>{const i=JSON.parse(r.dataset.instance||"{}");return i.instanceType===s.instanceType&&i.provider===s.provider}))?V(e):(document.querySelectorAll(".instance-row").forEach(s=>{s.style.display="none"}),e.forEach(s=>{const r=Array.from(document.querySelectorAll(".instance-row")).find(i=>{const n=JSON.parse(i.dataset.instance||"{}");return n.instanceType===s.instanceType&&n.provider===s.provider});r&&(r.style.display="",E.appendChild(r))})),J(),D()}function V(e){E&&(E.innerHTML="",e.forEach(o=>{const t=K(o);E.appendChild(t)}))}function K(e){const o=document.createElement("tr");return o.className="hover:bg-gray-50 instance-row",o.setAttribute("data-instance",JSON.stringify(e)),o.innerHTML=`
        <td class="col-provider px-3 py-3 whitespace-nowrap">
          <span class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-${O(e.provider)}-100 text-${O(e.provider)}-800">
            ${e.provider.toUpperCase()}
          </span>
        </td>
        <td class="col-instanceType px-3 py-3 whitespace-nowrap text-sm font-medium text-gray-900">
          <div class="max-w-48 truncate">${e.instanceType}</div>
        </td>
        <td class="col-type px-3 py-3 whitespace-nowrap">
          <div class="flex flex-col space-y-1">
            <span class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-blue-100 text-blue-800">
              ${e.type.replace("cloud-","").replace("dedicated-","").replace("-"," ")}
            </span>
            ${e.platform?`<span class="text-xs text-gray-500 capitalize">${e.platform}</span>`:""}
          </div>
        </td>
        <td class="col-vCPU px-3 py-3 whitespace-nowrap text-sm text-gray-900">${e.vCPU||"-"}</td>
        <td class="col-memory px-3 py-3 whitespace-nowrap text-sm text-gray-900">${e.memoryGiB?`${e.memoryGiB} GiB`:"-"}</td>
        <td class="col-architecture px-3 py-3 whitespace-nowrap text-sm text-gray-900">
          <span class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-800">
            ${e.architecture||"x86"}
          </span>
        </td>
        <td class="col-disk px-3 py-3 whitespace-nowrap text-sm text-gray-900">
          <div class="flex flex-col">
            <span>${e.diskSizeGB?`${e.diskSizeGB} GB`:"-"}</span>
            ${e.diskType?`<span class="text-xs text-gray-500">${e.diskType}</span>`:""}
          </div>
        </td>
        <td class="col-networkSpeed px-3 py-3 whitespace-nowrap text-sm text-gray-900">
          <div class="flex flex-col">
            <span>${e.network_speed||"-"}</span>
            ${e.network_speed?'<span class="text-xs text-gray-500">Connection</span>':""}
          </div>
        </td>
        <td class="col-network px-3 py-3 whitespace-nowrap">
          <div class="flex flex-col space-y-1" data-network-info="true">
            <span class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-cyan-100 text-cyan-800 pricing-mode-indicator">
              IPv4 + IPv6
            </span>
            <span class="text-xs text-gray-500 pricing-description">Standard pricing</span>
          </div>
        </td>
        <td class="col-priceHour px-3 py-3 whitespace-nowrap text-sm font-medium text-gray-900 pricing-hourly">
          <div class="flex flex-col">
            <span class="pricing-value" data-usd-price="${e.priceUSD_hourly||0}" data-eur-price="${e.priceEUR_hourly_net||0}">
              $${(e.priceUSD_hourly||e.priceEUR_hourly_net||0).toFixed(4)}
            </span>
          </div>
        </td>
        <td class="col-priceMonth px-3 py-3 whitespace-nowrap text-sm text-gray-900 pricing-monthly">
          <div class="flex flex-col">
            <span class="pricing-value" data-usd-price="${e.priceUSD_monthly||0}" data-eur-price="${e.priceEUR_monthly_net||0}">
              $${(e.priceUSD_monthly||e.priceEUR_monthly_net||0).toFixed(2)}
            </span>
          </div>
        </td>
        <td class="col-regions px-3 py-3 whitespace-nowrap text-sm text-gray-500">
          <div class="flex flex-wrap gap-1 max-w-32 relative regions-container" data-regions='${JSON.stringify(e.locationDetails||[])}'>
            ${j(e.locationDetails||e.regions||[])}
          </div>
        </td>
        <td class="col-description px-3 py-3 whitespace-nowrap text-sm text-gray-500 hidden">
          <div class="max-w-48 truncate" title="${e.description||""}">${e.description||"-"}</div>
        </td>
      `,o}function O(e){return{aws:"orange",azure:"blue",gcp:"green",hetzner:"red",oci:"purple",ovh:"indigo"}[e]||"gray"}function j(e){if(!e||e.length===0)return"-";const o=e.slice(0,3),t=Math.max(0,e.length-3);let s="";return o.forEach(r=>{typeof r=="string"?s+=`<span class="text-xs">${r}</span>`:r.code&&(s+=`
            <div class="relative group">
              <span class="inline-flex items-center space-x-1 cursor-pointer hover:bg-gray-100 px-1 py-0.5 rounded">
                <img src="https://flagsapi.com/${r.countryCode}/flat/16.png" alt="${r.country}" class="w-4 h-3 rounded-sm" loading="lazy" />
                <span class="text-xs">${r.code}</span>
              </span>
            </div>
          `)}),t>0&&(s+=`<span class="text-xs text-gray-400">+${t}</span>`),s}function N(){const e=document.querySelectorAll(".instance-row");let o=0;f===""?e.forEach(t=>{const s=t;s.style.display="",o++}):e.forEach(t=>{const s=t,r=JSON.parse(s.dataset.instance||"{}");G(r,f)?(s.style.display="",o++):s.style.display="none"}),h&&(h.textContent=`${o} instances`)}function G(e,o){return[e.instanceType,e.provider,e.type,e.description,e.architecture,e.diskType,e.cpu_description,e.ram_description,e.storage_description,...e.regions||[],...(e.locationDetails||[]).map(s=>[s.city,s.country,s.code]).flat()].some(s=>s&&s.toString().toLowerCase().includes(o))}function W(){const e=document.getElementById("instances-table");if(!e)return;e.querySelectorAll("th").forEach((t,s)=>{const r=document.createElement("div");r.className="resize-handle",r.style.cssText=`
          position: absolute;
          right: 0;
          top: 0;
          bottom: 0;
          width: 4px;
          cursor: col-resize;
          user-select: none;
          background: transparent;
        `,r.addEventListener("mousedown",i=>{i.preventDefault(),g=!0,S=t,document.body.style.cursor="col-resize",document.body.style.userSelect="none"}),t.style.position="relative",t.appendChild(r)}),document.addEventListener("mousemove",t=>{if(!g||!S)return;const s=S.getBoundingClientRect(),r=t.clientX-s.left;if(r>50){S.style.width=r+"px",S.style.minWidth=r+"px";const i=document.getElementById("instances-table");if(i){const n=Array.from(S.parentElement?.children||[]).indexOf(S);i.querySelectorAll("tbody tr").forEach(l=>{const a=l.children[n];if(a){const u=a.querySelector("div");u&&(u.style.maxWidth=r+"px",u.classList.remove("max-w-24","max-w-32","max-w-48"),r>200?u.classList.remove("truncate"):u.classList.add("truncate"))}})}}}),document.addEventListener("mouseup",()=>{g&&(g=!1,S=null,document.body.style.cursor="",document.body.style.userSelect="")})}window.addEventListener("providersSelectionChanged",async e=>{const o=e,{selectedProviders:t,action:s}=o.detail;if(s==="load"&&t.length>0)try{h&&(h.textContent="Loading providers...");const{loadSelectedProviders:r}=await Q(async()=>{const{loadSelectedProviders:n}=await import("./dynamic-loader.BHiLaTpp.js");return{loadSelectedProviders:n}},[]),i=await r(t);if(i.length>0){L=i,q(L),h&&(h.textContent=`${L.length} instances`);const n=P;Object.keys(n).length>0&&I(n),console.log(`🔄 Dynamically loaded ${i.length} instances from providers:`,t)}}catch(r){console.error("Error loading provider data:",r),h&&(h.textContent="Error loading data")}}),W(),setTimeout(()=>{const e=new CustomEvent("requestCurrencyData");window.dispatchEvent(e)},100)});

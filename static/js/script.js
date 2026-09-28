function toggleTheme(){
    const current=document.documentElement.dataset.theme||"light";
    const next=current==="dark"?"light":"dark";
    document.documentElement.dataset.theme=next;
    localStorage.setItem("skillgap-theme",next);
}
function toggleSidebar(){document.getElementById("sidebar")?.classList.toggle("open");}

document.addEventListener("DOMContentLoaded",()=>{
    const input=document.getElementById("resume"), name=document.getElementById("file-name");
    if(input&&name){
        input.addEventListener("change",()=>{
            const file=input.files[0];
            if(!file){name.textContent="Choose your resume";return;}
            const ok=/\.(pdf|docx)$/i.test(file.name);
            if(!ok||file.size>8*1024*1024){
                input.value=""; name.textContent="Choose your resume";
                alert(!ok?"Please select a PDF or DOCX file.":"Maximum file size is 8 MB.");
                return;
            }
            name.textContent=file.name;
        });
    }
    document.querySelectorAll(".flash").forEach(x=>setTimeout(()=>x.classList.add("fade-out"),5000));
});

function drawTrendChart(id,data){
    const canvas=document.getElementById(id); if(!canvas||!window.Chart)return;
    new Chart(canvas,{type:"line",data:{labels:data.map(x=>x.date),datasets:[{label:"Match %",data:data.map(x=>x.score),tension:.35,fill:true}]},options:{responsive:true,plugins:{legend:{display:false}},scales:{y:{min:0,max:100}}}});
}
function drawProgressChart(id,data){
    const canvas=document.getElementById(id); if(!canvas||!window.Chart)return;
    new Chart(canvas,{type:"line",data:{labels:data.map(x=>x.date),datasets:[
        {label:"Overall",data:data.map(x=>x.score),tension:.35},
        {label:"Skill",data:data.map(x=>x.skill),tension:.35},
        {label:"Keywords",data:data.map(x=>x.keyword),tension:.35}
    ]},options:{responsive:true,scales:{y:{min:0,max:100}}}});
}

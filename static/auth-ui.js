// Signed-in account: shown at the sidebar foot; admin-only tools stay hidden for other roles.
let currentUser=null;
function applyUser(user){currentUser=user;$('userName').textContent=user.display;$('userRole').textContent=user.role_name;$('userAvatar').textContent=(user.display||'?').slice(0,1).toUpperCase();document.body.classList.toggle('is-admin',!!user.admin);}
async function loadUser(){try{const r=await fetch('/api/auth/me');if(r.status===401){location.href='/login';return;}applyUser((await r.json()).user);}catch(e){toast('本地服务未连接，请重新启动工作台。')}}
async function logout(){if(state?.running&&!confirm('任务正在执行，退出登录不会中断任务。确定退出登录？'))return;await fetch('/api/auth/logout',{method:'POST',headers:{'Content-Type':'application/json','X-Agent-Demo':'1'},body:'{}'}).catch(()=>{});location.href='/login';}
const openSkillStudioAsAdmin=openSkillStudio;
openSkillStudio=function(){if(!currentUser?.admin)return toast('技能开发与管理仅限管理员使用');return openSkillStudioAsAdmin();};
loadUser();

import os

with open('frontend/App.tsx', 'r', encoding='utf-8') as f:
    content = f.read()

content = content.replace('Mnemo', 'Nexmem')
content = content.replace('mnemo', 'nexmem')
content = content.replace('@nexmem/sdk', 'nexmem-js')
content = content.replace('mem_', 'nxm_')
content = content.replace('Memory infrastructure for the agentic web', 'Next-gen memory for AI agents.')

content = content.replace('<style>{GLOBAL_CSS}</style>', '')

content = content.replace('function GlowButton({ children, className = "", onClick }) {', '''function GlowButton({ children, className = "", onClick }) {
  const defaultOnClick = () => window.open('https://api.nexmem.ai/auth/signup', '_blank');
  const handleClick = onClick || defaultOnClick;''')
content = content.replace('<button onClick={onClick}', '<button onClick={handleClick}')

content = '"use client";\n' + content

with open('nexmem-landing/app/page.tsx', 'w', encoding='utf-8') as f:
    f.write(content)

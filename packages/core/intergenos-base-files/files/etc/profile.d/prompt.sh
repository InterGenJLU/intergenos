# InterGenOS shell prompts
# Blue brackets, white delimiters, green path
# User: green username + $    Root: red username + #

if [ "$(id -u)" -eq 0 ]; then
  PS1='\[\e[1;34m\][\[\e[m\]\[\e[1;31m\]\u\[\e[m\]\[\e[1;34m\]@\[\e[m\]\[\e[1;37m\]\H\[\e[m\]\[\e[1;34m\]]\[\e[m\]\[\e[1;34m\][\[\e[m\]\[\e[1;37m\]<\[\e[m\]\[\e[1;32m\]\w\[\e[m\]\[\e[1;37m\]>\[\e[m\]\[\e[1;34m\]]\[\e[m\]\[\e[1;37m\]:\[\e[m\]\[\e[1;31m\]#\[\e[m\] '
else
  PS1='\[\e[1;34m\][\[\e[m\]\[\e[1;32m\]\u\[\e[m\]\[\e[1;34m\]@\[\e[m\]\[\e[1;37m\]\H\[\e[m\]\[\e[1;34m\]]\[\e[m\]\[\e[1;34m\][\[\e[m\]\[\e[1;37m\]<\[\e[m\]\[\e[1;32m\]\w\[\e[m\]\[\e[1;37m\]>\[\e[m\]\[\e[1;34m\]]\[\e[m\]\[\e[1;37m\]:\[\e[m\]\[\e[1;32m\]$\[\e[m\] '
fi
export PS1

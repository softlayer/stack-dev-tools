HISTFILE=~/.histfile
HISTSIZE=5000
SAVEHIST=5000
setopt appendhistory

export CLICOLOR="YES"
export LSCOLORS="ExGxFxdxCxDxDxhbadExEx"

autoload -Uz compinit
compinit

precmd () {
 PROMPT="${HOST%.*.*}:%~ %n%# "
 Z_TITLE="${USER}@${HOST%.*.*}:${PWD/${HOME}/~}"
 echo -ne "\e]2;${Z_TITLE}\a"
 echo -ne "\e]1;${Z_TITLE}\a"
}

alias ll='ls -l'

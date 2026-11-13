/* bhost: run a Lua script on a given LuaJIT runtime (e.g. the one bundled with the game),
 * so the engine uses exactly the same VM, string hashing and table iteration order. */
#include <stdio.h>
#include "lua.h"
#include "lauxlib.h"
#include "lualib.h"

int main(int argc, char **argv) {
  if (argc < 2) { fprintf(stderr, "usage: bhost script.lua [args]\n"); return 2; }
  lua_State *L = luaL_newstate();
  if (!L) { fprintf(stderr, "cannot create Lua state\n"); return 1; }
  luaL_openlibs(L);
  lua_newtable(L);
  for (int i = 0; i < argc; i++) { lua_pushstring(L, argv[i]); lua_rawseti(L, -2, i - 1); }
  lua_setglobal(L, "arg");
  if (luaL_loadfile(L, argv[1]) || lua_pcall(L, 0, 0, 0)) {
    fprintf(stderr, "bhost: %s\n", lua_tostring(L, -1));
    lua_close(L);
    return 1;
  }
  lua_close(L);
  return 0;
}

package com.studybot.app.ui

import android.content.Context
import android.util.AttributeSet
import com.google.android.material.bottomnavigation.BottomNavigationView

/**
 * P5：底部第 6 个 tab「笔记」。
 *
 * Material 的 BottomNavigationView 写死最多 5 项（NavigationBarMenu.addInternal 在
 * size()+1 > getMaxItemCount() 时直接抛 IllegalArgumentException），第 6 项会让
 * setContentView 崩掉。这里只把上限抬到 8，其余行为全部沿用父类。
 */
class SixTabBottomNavView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0,
) : BottomNavigationView(context, attrs, defStyleAttr) {

    override fun getMaxItemCount(): Int = 8
}

package com.studybot.app.ui

import android.os.Bundle
import android.view.View
import androidx.fragment.app.Fragment
import com.studybot.app.MainActivity
import com.studybot.app.R
import com.studybot.app.databinding.FragmentListBinding

interface Refreshable {
    fun reload()
}

/** 四页共用的壳：下拉刷新 + ScrollView + 内容容器。 */
abstract class BaseListFragment : Fragment(R.layout.fragment_list), Refreshable {

    private var _binding: FragmentListBinding? = null
    protected val binding: FragmentListBinding get() = _binding!!

    protected fun viewAlive(): Boolean = _binding != null

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        _binding = FragmentListBinding.bind(view)
        binding.refresh.setOnRefreshListener {
            val act = activity as? MainActivity
            if (act == null) {
                binding.refresh.isRefreshing = false
            } else {
                act.syncFromPull { binding.refresh.isRefreshing = false }
            }
        }
        reload()
    }

    override fun onDestroyView() {
        super.onDestroyView()
        _binding = null
    }
}
